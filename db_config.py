import asyncpg

class DbConfig:
    def __init__(self):
        self.DB_HOST: str = "ep-rough-morning-ahriou9i-pooler.c-3.us-east-1.aws.neon.tech"
        self.DB_PORT: int = 5432
        self.DB_NAME: str = "neondb"
        self.DB_USER: str = "neondb_owner"
        self.DB_PASSWORD: str = "npg_irKGbJdc1v3L"
    
    def get_db_connection_async(self):
        return asyncpg.connect(
            host=self.DB_HOST,
            port=self.DB_PORT,
            user=self.DB_USER,
            password=self.DB_PASSWORD,
            database=self.DB_NAME
        )  
    
    def get_db_connection_pool_async(self):
        return asyncpg.create_pool(
            host=self.DB_HOST,
            port=self.DB_PORT,
            user=self.DB_USER,
            password=self.DB_PASSWORD,
            database=self.DB_NAME
        )