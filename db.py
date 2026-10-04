import sqlite3

conn = sqlite3.connect("dispatch.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plate TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL,
    capacity_kg INTEGER NOT NULL
)
""")

cur.execute(
    "INSERT OR IGNORE INTO vehicles (plate, vehicle_type, capacity_kg) VALUES (?, ?, ?)",
    ("TN01AB1234", "truck", 5000),
)
conn.commit()

cur.execute("SELECT id, plate, vehicle_type, capacity_kg FROM vehicles")
for row in cur.fetchall():
    print(row)

conn.close()