from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    API_ID: int
    API_HASH: str
   
    REF_ID: str = '339631649'

    USE_RANDOM_DELAY_IN_RUN: bool = False
    RANDOM_DELAY_IN_RUN: list[int] = [0, 39515]

    HOLD_COIN: list[int] = [585, 600]
    SWIPE_COIN: list[int] = [2000, 3000]
    SQUAD_ID_LEAVE: int = 2132956701
    SQUAD_ID_JOIN: list[int] = [2007141958, 1006503122, 2132956701, 2212658999]

    SLEEP_TIME: list[int] = [31600, 62400]
    
    USE_PROXY: bool = False


settings = Settings()


