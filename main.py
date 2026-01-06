import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from neo4j import GraphDatabase
from contextlib import asynccontextmanager
import uvicorn

# http://127.0.0.1:8000/

# настройки подключения к Neo4j
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = ("neo4j", os.getenv("NEO4J_PASSWORD", "mypassword"))


class Neo4jService:
    def __init__(self):
        try:
            self.driver = GraphDatabase.driver(URI, auth=AUTH)
            self.driver.verify_connectivity()
            print("Успешное подключение к Neo4j!")
        except Exception as e:
            print(f"ОШИБКА ПОДКЛЮЧЕНИЯ: {e}")
            self.driver = None

    def close(self):
        if self.driver:
            self.driver.close()

    def _run_query(self, query, parameters=None):
        """Вспомогательный метод для выполнения запросов"""
        if not self.driver:
            return None
        with self.driver.session() as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def _run_single(self, query, parameters=None):
        """Вспомогательный метод для получения одной записи"""
        if not self.driver:
            return None
        with self.driver.session() as session:
            result = session.run(query, parameters or {}).single()
            return result.data() if result else None

    # МЕТОДЫ ДЛЯ РАБОТЫ С ДАННЫМИ

    def get_tournaments(self):
        """Для демонстрации списка турниров"""
        query = """
        MATCH (t:Tournament) 
        RETURN t.id AS id, t.title AS title, t.stage AS stage 
        ORDER BY t.stage, t.title
        """
        return self._run_query(query)

    def get_tournament_questions(self, t_id: str):
        """Для демонстрации вопросов конкретного турнира"""
        query = """
        MATCH (t:Tournament)
        WHERE t.id = toInteger($t_id)
        MATCH (t)-[:HAS_QUESTION]->(q:Question)
        OPTIONAL MATCH (author:Person)-[:WROTE]->(q)
        RETURN q.uid AS uid, q.text AS text, q.answer AS answer, 
               q.number AS number, author.name AS author
        ORDER BY q.number
        """
        return self._run_query(query, {"t_id": t_id})

    def get_question_details(self, q_uid: str, t_id: str):
        query = """
        MATCH (q:Question {uid: $uid})
        OPTIONAL MATCH (author:Person)-[:WROTE]->(q)
        OPTIONAL MATCH (team:Team)-[ans:ANSWERED {tournament_id: toInteger($t_id)}]->(q)

        WITH q, author, 
             collect({team_id: team.id, team: team.name, city: team.city, correct: ans.is_correct}) AS raw_results

        // фильтруем пустые результаты
        WITH q, author, 
             [r IN raw_results WHERE r.team IS NOT NULL] AS results

        // считаем статистику
        WITH q, author, results,
             size(results) AS total,
             size([r IN results WHERE r.correct = true]) AS correct_count

        RETURN q.text AS text, 
               q.answer AS answer, 
               author.name AS author,
               results,
               {
                   total_teams: total,
                   correct_count: correct_count,
                   accuracy_percent: CASE WHEN total > 0 
                                     THEN round(toFloat(correct_count) / total * 100, 2) 
                                     ELSE 0 END
               } AS stats
        """
        return self._run_single(query, {"uid": q_uid, "t_id": t_id})

    def get_team_stats(self, team_id: str, t_id: str):
        """Для демонстрации статистики команды в конкретном турнире"""
        query = """
        MATCH (tourn:Tournament) WHERE tourn.id = toInteger($t_id)
        MATCH (tourn)-[:HAS_QUESTION]->(q:Question)

        // среднее по турниру
        OPTIONAL MATCH (q)<-[all_ans:ANSWERED {tournament_id: toInteger($t_id)}]-()
        WITH tourn, q, count(all_ans) AS total_attempts, 
             sum(CASE WHEN all_ans.is_correct THEN 1 ELSE 0 END) AS total_correct

        // результат конкретной команды
        OPTIONAL MATCH (team:Team {id: toInteger($team_id)})-[team_ans:ANSWERED {tournament_id: toInteger($t_id)}]->(q)

        RETURN q.number AS num,
               CASE WHEN total_attempts > 0 
                    THEN round(toFloat(total_correct)/total_attempts * 100, 1) 
                    ELSE 0 END AS avg_accuracy,
               CASE WHEN team_ans.is_correct THEN 100 ELSE 0 END AS team_result
        ORDER BY q.number
        """
        return self._run_query(query, {"team_id": team_id, "t_id": t_id})

    def get_leaderboard(self, t_id: str):
        """Для визуализации лидерборда"""
        query = """
        MATCH (tourn:Tournament) WHERE tourn.id = toInteger($t_id)
        MATCH (tourn)-[:HAS_QUESTION]->(q:Question)
        MATCH (q)<-[ans:ANSWERED {tournament_id: toInteger($t_id)}]-(t:Team)
        WHERE ans.is_correct = true
        RETURN t.id AS team_id, 
               t.name AS team, 
               t.city AS city, 
               count(ans) AS score
        ORDER BY score DESC, team ASC
        """
        return self._run_query(query, {"t_id": t_id})

    def get_team_roster(self, team_id: str, t_id: str):
        """Для демонстрации состава команды в конкретном турнире"""
        query = """
        MATCH (p:Person)-[r:PLAYED_IN {tournament_id: toInteger($t_id)}]->(t:Team {id: toInteger($team_id)})
        RETURN p.name AS name
        """
        return self._run_query(query, {"team_id": team_id, "t_id": t_id})

    def search_teams(self, q: str):
        """Для глобального поиска команд при аналитике"""
        query = """
        MATCH (t:Team)
        WHERE toLower(t.name) CONTAINS toLower($q)
        RETURN t.id AS id, t.name AS name, t.city AS city
        LIMIT 10
        """
        return self._run_query(query, {"q": q})

    def get_team_global_stats(self, team_id: str):
        """Для глобальной статистики по команде при аналитике"""
        query = """
        MATCH (t:Team {id: toInteger($team_id)})

        OPTIONAL MATCH (p:Person)-[:PLAYED_IN]->(t)
        WITH t, collect(DISTINCT p.name) AS roster

        OPTIONAL MATCH (t)-[ans:ANSWERED]->(q:Question)
        WITH t, roster,
             count(DISTINCT ans.tournament_id) AS total_t, 
             sum(CASE WHEN ans.is_correct THEN 1 ELSE 0 END) AS total_c,
             collect(DISTINCT ans.tournament_id) AS t_ids

        UNWIND t_ids AS tid
        MATCH (other:Team)-[oa:ANSWERED {tournament_id: tid}]->(:Question)
        WHERE oa.is_correct = true
        WITH t, roster, total_t, total_c, tid, other, count(oa) AS score
        WITH t, roster, total_t, total_c, tid, collect({id: other.id, s: score}) AS lb
        WITH roster, total_t, total_c, [x IN lb WHERE x.id = t.id][0].s AS my_score, lb, t
        WITH roster, total_t, total_c, size([x IN lb WHERE x.s > my_score]) + 1 AS rank, t

        RETURN 
            t.name AS name,
            t.city AS city,
            roster,
            total_t AS total_tournaments,
            total_c AS total_correct,
            CASE WHEN total_t > 0 THEN round(toFloat(total_c)*100/(total_t*36), 1) ELSE 0 END AS accuracy,
            round(avg(rank), 1) AS avg_rank,
            min(rank) AS best_rank,
            max(rank) AS worst_rank
        """
        return self._run_single(query, {"team_id": team_id}) or {}

    def get_team_chart_stats(self, team_id: str):
        """Для глобальной статистики по команде при визуализации графика"""
        query = """
        MATCH (t:Team {id: toInteger($team_id)})
        MATCH (tourn:Tournament)

        // ищем ответы команды в конкретном турнире
        OPTIONAL MATCH (t)-[ans:ANSWERED {tournament_id: tourn.id}]->(:Question)

        WITH tourn, 
             sum(CASE WHEN ans.is_correct THEN 1 ELSE 0 END) AS correct_count,
             count(ans) AS total_questions

        // оставляем только те турниры, где было задан хотя бы 1 вопрос (= команда участвовала)
        WHERE total_questions > 0

        RETURN tourn.title AS stage_title,
               tourn.stage AS stage_number,
               correct_count,
               total_questions
        ORDER BY tourn.stage
        """
        return self._run_query(query, {"team_id": team_id}) or []

    def get_team_questions_history(self, team_id: str):
        """Для истории вопросов при аналитике"""
        query = """
        MATCH (t:Team {id: toInteger($team_id)})-[ans:ANSWERED]->(q:Question)
        MATCH (tourn:Tournament {id: ans.tournament_id})
        RETURN tourn.title AS tournament,
               q.number AS number,
               q.text AS text,
               q.answer AS answer,
               ans.is_correct AS is_correct
        ORDER BY tourn.id ASC, q.number ASC
        """
        return self._run_query(query, {"team_id": team_id})


db = Neo4jService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    db.close()


app = FastAPI(title="ОВСЧ Аналитика API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def read_index():
    return FileResponse('static/index.html')


@app.get("/tournaments")
async def get_tournaments():
    return db.get_tournaments()


@app.get("/tournament/{t_id}/questions")
async def get_tournament_questions(t_id: str):
    return db.get_tournament_questions(t_id)


@app.get("/question/{q_uid}")
async def question_page(q_uid: str, t_id: str):
    data = db.get_question_details(q_uid, t_id)
    # if not data:
    #     raise HTTPException(status_code=404, detail="Вопрос не найден")
    return data


@app.get("/team_stats/{team_id}/{t_id}")
async def get_team_stats(team_id: str, t_id: str):
    return db.get_team_stats(team_id, t_id)


@app.get("/tournament/{t_id}/leaderboard")
async def get_leaderboard(t_id: str):
    return db.get_leaderboard(t_id)


@app.get("/team/{team_id}/roster/{t_id}")
async def get_team_roster(team_id: str, t_id: str):
    return db.get_team_roster(team_id, t_id)


@app.get("/search_teams")
async def search_teams(q: str):
    return db.search_teams(q)


@app.get("/team_global_stats/{team_id}")
async def get_team_global_stats(team_id: str):
    return db.get_team_global_stats(team_id)


@app.get("/team_questions_history/{team_id}")
async def get_team_questions_history(team_id: str):
    return db.get_team_questions_history(team_id)


@app.get("/team_chart_stats/{team_id}")
async def get_team_chart_stats(team_id: str):
    return db.get_team_chart_stats(team_id)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
