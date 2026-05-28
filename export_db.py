import pandas as pd
from sqlalchemy import create_engine

OLD_DB = "postgresql://pressjobs_db_user:g570K8YV5VHv2ZtansjBmDlvFYZTRY1F@dpg-d7fvj8dckfvc73db1a2g-a.oregon-postgres.render.com/pressjobs_db"

engine = create_engine(OLD_DB)

tables = ["users", "posts"]  # غيّرها حسب مشروعك

with engine.connect() as conn:
    for table in tables:
        df = pd.read_sql(f"SELECT * FROM {table}", conn)
        df.to_json(f"{table}.json", orient="records")

print("Export done")
