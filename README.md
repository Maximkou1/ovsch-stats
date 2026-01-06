# База вопросов ОВСЧ 24/25
ОВСЧ — Открытый Всеобщий Синхронный Чемпионат — самый популярный турнир по спортивным интеллектуальным играм, который ежегодно проходит в 6 этапов и традиционно играется большим количеством команд в двух форматах, синхронно и асинхронно. После завершения турнира информация о нём распределяется между двумя независимыми ресурсами:

* https://rating.chgk.info — турнирный сайт, где хранятся данные о сыгравших командах и о номерах вопросов, на которые ответила та или иная команда;
* https://gotquestions.online — база вопросов, где хранятся тексты вопросов и ответы на них.

Этот проект — попытка создать удобную платформу для аналитики, совместив данные из двух источников, чтобы можно было смотреть, какие конкретно команды/игроки ответили на определённый вопрос.

Для создания базы данных было принято решение использовать графовую СУБД Neo4j, позволяющую хранить отношения вида *вопрос->турнир* и *игрок->команда*.
Для создания первичного датасета была получена необходимая информация о 6 этапах ОВСЧ сезона 2024/25 с помощью [API](https://api.rating.chgk.info) и дампа базы вопросов. Затем датасет был переведён в необходимый формат. Было принято решение создать 4 типа узлов и 5 типов связей.

Узлы:
* Person {id, name}
* Team {id, name, city}
* Question {uid, text, answer, number}
* Tournament {id, title, stage, date, type}

Связи:
* (Team)-[:ANSWERED {is_correct: bool, tournament_id: int}]->(Question)
* (Person)-[:PLAYED_IN {tournament_id: int}]->(Team)
* (Team)-[:PARTICIPATED]->(Tournament)
* (Tournament)-[:HAS_QUESTION]->(Question)
* (Person)-[:WROTE]->(Question)

<img src="https://github.com/Maximkou1/ovsch-stats/raw/main/images/db_schema.png" width="500">

# Для запуска
1. скопируйте файл окружения командой ```cp env.example .env``` (опционально: замените пароль в файле .env)
2. скачайте docker-compose.yml
3. в терминале выполните команду ```docker compose up```
4. http://localhost:7474 — графический интерфейс, http://localhost:8000 — приложение

# Примеры запросов
- Вывести названия команд вместе с id
```
MATCH (t:Team)
RETURN t.name AS TeamName, t.id AS TeamID
```

- Вывести граф вопросов, которые не взяла конкретная команда, и авторов этих вопросов
```
MATCH (t:Team {id: 87778})-[r:ANSWERED {is_correct: false}]->(q:Question)
MATCH (author:Person)-[:WROTE]->(q)
RETURN t, r, q, author
```
<img src="https://github.com/Maximkou1/ovsch-stats/raw/main/images/graph.png" width="500">

- Вывести турниры от самого простого к самому сложному
```
MATCH (t:Tournament)-[:HAS_QUESTION]->(q:Question)
OPTIONAL MATCH (q)<-[ans:ANSWERED]-(team:Team) 
WHERE ans.tournament_id = t.id

WITH t, q,
     count(ans) AS total_attempts,
     sum(CASE WHEN ans.is_correct THEN 1 ELSE 0 END) AS correct_answers

WITH t.stage AS Stage, 
     t.title AS Title,
     avg(toFloat(correct_answers) / total_attempts) * 100 AS avg_accuracy

RETURN 
    Stage, 
    Title,
    round(avg_accuracy, 2) AS AnsweredProportion
ORDER BY avg_accuracy DESC
```

- BFS для поиска кратчайшего пути между двумя игроками
```
MATCH (p1:Person {name: "Альберт Агалян"}), (p2:Person {name: "Николай Афонин"})
MATCH path = shortestPath((p1)-[:PLAYED_IN*..6]-(p2))
OPTIONAL MATCH (p1)-[:PLAYED_IN]->(t1:Team)
OPTIONAL MATCH (p2)-[:PLAYED_IN]->(t2:Team)
RETURN path, t1, t2
```
<img src="https://github.com/Maximkou1/ovsch-stats/raw/main/images/bfs.png" width="500">
