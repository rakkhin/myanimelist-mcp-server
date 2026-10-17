from enum import Enum


class AnimeRanking(Enum):
    ALL = "all"
    AIRING = "airing"
    UPCOMING = "upcoming"
    TV = "tv"
    OVA = "ova"
    MOVIE = "movie"
    SPECIAL = "special"
    BYPOPULARITY = "bypopularity"
    FAVORITE = "favorite"


class Season(Enum):
    WINTER = "winter"
    SPRING = "spring"
    SUMMER = "summer"
    FALL = "fall"


class SeasonSort(Enum):
    SCORE = "anime_score"
    NUM_LIST_USERS = "anime_num_list_users"


class AnimeStatus(Enum):
    WATCHING = "watching"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLANNING = "plan_to_watch"


class AnimeStatusSort(Enum):
    LIST_SCORE = "list_score"
    LIST_UPDATED_AT = "list_updated_at"
    ANIME_TITLE = "anime_title"
    ANIME_START_DATE = "anime_start_date"


class AnimeFields(Enum):
    ID = "id"
    TITLE = "title"
    MAIN_PICTURE = "main_picture"
    ALTERNATIVE_TITLES = "alternative_titles"
    START_DATE = "start_date"
    END_DATE = "end_date"
    SYNOPSIS = "synopsis"
    MEAN = "mean"
    RANK = "rank"
    POPULARITY = "popularity"
    NUM_LIST_USERS = "num_list_users"
    NUM_SCORING_USERS = "num_scoring_users"
    NSFW = "nsfw"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    MEDIA_TYPE = "media_type"
    STATUS = "status"
    GENRES = "genres"
    MY_LIST_STATUS = "my_list_status"
    NUM_EPISODES = "num_episodes"
    START_SEASON = "start_season"
    BROADCAST = "broadcast"
    SOURCE = "source"
    AVERAGE_EPISODE_DURATION = "average_episode_duration"
    RATING = "rating"
    PICTURES = "pictures"
    BACKGROUND = "background"
    RELATED_ANIME = "related_anime"
    RELATED_MANGA = "related_manga"
    RECOMMENDATIONS = "recommendations"
    STUDIOS = "studios"
    STATISTICS = "statistics"


class MangaFields(Enum):
    ID = "id"
    TITLE = "title"
    MAIN_PICTURE = "main_picture"
    ALTERNATIVE_TITLES = "alternative_titles"
    START_DATE = "start_date"
    END_DATE = "end_date"
    SYNOPSIS = "synopsis"
    MEAN = "mean"
    RANK = "rank"
    POPULARITY = "popularity"
    NUM_LIST_USERS = "num_list_users"
    NUM_SCORING_USERS = "num_scoring_users"
    NSFW = "nsfw"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    MEDIA_TYPE = "media_type"
    STATUS = "status"
    GENRES = "genres"
    MY_LIST_STATUS = "my_list_status"
    NUM_VOLUMES = "num_volumes"
    NUM_CHAPTERS = "num_chapters"
    AUTHORS = "authors"
    PICTURES = "pictures"
    BACKGROUND = "background"
    RELATED_ANIME = "related_anime"
    RELATED_MANGA = "related_manga"
    RECOMMENDATIONS = "recommendations"
    SERIALIZATION = "serialization"


class MangaRanking(Enum):
    ALL = "all"
    MANGA = "manga"
    NOVEL = "novels"
    ONE_SHOT = "oneshots"
    DOUJIN = "doujin"
    MANHWA = "manhwa"
    MANHUA = "manhua"
    BYPOPULARITY = "bypopularity"
    FAVORITE = "favorite"


class MangaStatus(Enum):
    READING = "reading"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"
    DROPPED = "dropped"
    PLANNING = "plan_to_read"


class MangaStatusSort(Enum):
    LIST_SCORE = "list_score"
    LIST_UPDATED_AT = "list_updated_at"
    MANGA_TITLE = "manga_title"
    MANGA_START_DATE = "manga_start_date"
