import os
import json
from neo4j import GraphDatabase

DATA_FILE = "graph_data_for_neo4j_anonymized.json"

# bolt://db:7687
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
PASSWORD = os.getenv("NEO4J_PASSWORD", "mypassword")
AUTH = ("neo4j", PASSWORD)


class ChgkLoader:
    def __init__(self, uri, auth):
        self.driver = GraphDatabase.driver(uri, auth=auth)

    def close(self):
        self.driver.close()

    def nuke_database(self):
        print("*** Полная очистка базы...")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

            # удаляем старые индексы/констрейнты
            try:
                constraints = session.run("SHOW CONSTRAINTS")
                for record in constraints:
                    session.run(f"DROP CONSTRAINT {record['name']}")
                indexes = session.run("SHOW INDEXES")
                for record in indexes:
                    if record['type'] != 'LOOKUP':  # LOOKUP удалять нельзя
                        try:
                            session.run(f"DROP INDEX {record['name']}")
                        except:
                            pass
            except Exception as e:
                print(f"ОШИБКА: {e}")

    def create_constraints(self):
        print("*** Создание индексов и ограничений...")
        with self.driver.session() as session:
            # проверяем, что все ID уникальны — важно для MERGE
            session.run("CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE")
            session.run("CREATE CONSTRAINT team_id IF NOT EXISTS FOR (t:Team) REQUIRE t.id IS UNIQUE")
            session.run("CREATE CONSTRAINT tourn_id IF NOT EXISTS FOR (t:Tournament) REQUIRE t.id IS UNIQUE")
            session.run("CREATE CONSTRAINT quest_uid IF NOT EXISTS FOR (q:Question) REQUIRE q.uid IS UNIQUE")

    def batch_load(self, query, data, batch_size=1000):
        total = len(data)
        if total == 0:
            return

        with self.driver.session() as session:
            for i in range(0, total, batch_size):
                batch = data[i: i + batch_size]
                session.run(query, batch=batch)
                print(f"   ...обработано {min(i + batch_size, total)} / {total}")

    def load_data(self):
        print(f"*** Чтение файла {DATA_FILE}...")
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            graph_data = json.load(f)

        nodes = graph_data["nodes"]
        rels = graph_data["relationships"]

        print("\n=== ЗАГРУЗКА УЗЛОВ ===")

        # 1. турниры
        print("*** Загрузка Турниров...")
        q_tourn = """
        UNWIND $batch AS row
        MERGE (t:Tournament {id: row.id})
        SET t.title = row.title, 
            t.stage = row.stage, 
            t.date = row.date,
            t.type = row.type
        """
        self.batch_load(q_tourn, nodes["Tournament"])

        # 2. команды
        print("*** Загрузка Команд...")
        q_team = """
        UNWIND $batch AS row
        MERGE (t:Team {id: row.id})
        SET t.name = row.name, 
            t.city = row.city
        """
        self.batch_load(q_team, nodes["Team"])

        # 3. вопросы
        print("*** Загрузка Вопросов...")
        q_quest = """
        UNWIND $batch AS row
        MERGE (q:Question {uid: row.uid})
        SET q.text = row.text, 
            q.answer = row.answer,
            q.number = row.number,
            q.stage = row.stage
        """
        self.batch_load(q_quest, nodes["Question"])

        # 4. люди (игроки + авторы)
        print("*** Загрузка Людей...")
        q_person = """
        UNWIND $batch AS row
        MERGE (p:Person {id: row.id})
        SET p.name = row.name
        """
        self.batch_load(q_person, nodes["Person"])

        print("\n=== СОЗДАНИЕ СВЯЗЕЙ ===")

        # 1. вопросы в турнире: Турнир -> Вопросы
        print("*** Связь: вопросы в турнире")
        q_tourn_quest = """
        UNWIND $batch AS row
        MATCH (t:Tournament {stage: row.stage})
        MATCH (q:Question {stage: row.stage})
        MERGE (t)-[:HAS_QUESTION]->(q)
        """
        # берём уникальные этапы из узлов турниров
        stages_batches = [{"stage": n["stage"]} for n in nodes["Tournament"]]
        self.batch_load(q_tourn_quest, stages_batches)

        # 2. авторство: Человек -> Вопрос
        print("*** Связь: авторство")
        q_wrote = """
        UNWIND $batch AS row
        MATCH (p:Person {id: row.person_id})
        MATCH (q:Question {uid: row.question_id})
        MERGE (p)-[:WROTE]->(q)
        """
        self.batch_load(q_wrote, rels["WROTE"])

        # 3. участие команды: Команда —> Турнир
        print("Связь: участие команды в турнире")
        q_part = """
        UNWIND $batch AS row
        MATCH (t:Team {id: row.team_id})
        MATCH (tourn:Tournament {id: row.tournament_id})
        MERGE (t)-[r:PARTICIPATED]->(tourn)
        SET r.position = row.position,
            r.total_correct = row.total_correct
        """
        self.batch_load(q_part, rels["PARTICIPATED"])

        # 4. составы команд: Человек —> Команда
        print("Связь: составы команд")
        q_played = """
        UNWIND $batch AS row
        MATCH (p:Person {id: row.person_id})
        MATCH (t:Team {id: row.team_id})
        MERGE (p)-[r:PLAYED_IN {tournament_id: row.tournament_id}]->(t)
        SET r.role = row.role
        """
        self.batch_load(q_played, rels["PLAYED_IN"])

        # 5. ответы команд: Команда —> Вопрос
        print("Связь: ответы команд")
        q_ans = """
        UNWIND $batch AS row
        MATCH (t:Team {id: row.team_id})
        MATCH (q:Question {uid: row.question_id})
        MERGE (t)-[r:ANSWERED {tournament_id: row.tournament_id}]->(q)
        SET r.is_correct = row.is_correct
        """
        self.batch_load(q_ans, rels["ANSWERED"], batch_size=2000)

        print("\n*** Готово! База данных успешно построена.")


if __name__ == "__main__":
    loader = ChgkLoader(URI, AUTH)
    try:
        loader.nuke_database()
        loader.create_constraints()
        loader.load_data()
    except Exception as e:
        print(f"ОШИБКА: {e}")
    finally:
        loader.close()
