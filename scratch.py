import sqlite3
db = sqlite3.connect('data/processed/analyzer.sqlite')
print(db.execute("SELECT COUNT(*) FROM scraped_items WHERE source='kwork'").fetchone())
