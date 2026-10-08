"""Загружает тестовые данные (test_data.sql) в базу vyazanie.db.
Запуск из папки проекта: python load_test_data.py
Повторный запуск ничего не дублирует."""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect(os.path.join(BASE, "vyazanie.db"))
db.executescript(open(os.path.join(BASE, "schema.sql"), encoding="utf-8").read())
if db.execute("SELECT COUNT(*) FROM users WHERE username='test_anna'").fetchone()[0]:
    print("Тестовые данные уже загружены.")
else:
    db.executescript(open(os.path.join(BASE, "test_data.sql"), encoding="utf-8").read())
    db.commit()
    print("Тестовые данные загружены.")
for t in ("categories", "users", "patterns", "comments", "favorites"):
    print(f"{t}: {db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]} записей")
db.close()
