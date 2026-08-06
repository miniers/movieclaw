"""终验发现缺陷的回归测试（对照修复清单，防止翻案倒退）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from jellyfin.helpers import jf_login
from movieclaw_jellyfin.ids import episode_guid, item_guid, library_guid, season_guid

TICKS_PER_MS = 10_000


def test_pascalcase_query_params(client: TestClient, seeded: dict) -> None:
    """致命缺陷#1：PascalCase 客户端的 query 键必须被归一化。"""
    token = jf_login(client)
    body = client.get(
        "/Items",
        params={
            "ApiKey": token,
            "ParentId": library_guid(seeded["tv_lib"]),  # 大写 P
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "SortBy": "ParentIndexNumber,IndexNumber",
            "Limit": "2",
        },
    ).json()
    assert body["TotalRecordCount"] == 3
    assert len(body["Items"]) == 2
    assert body["Items"][0]["Type"] == "Episode"

    # Static/MediaSourceId 大小写混写也必须能播
    guid = item_guid(seeded["movie"])
    info = client.post(f"/Items/{guid}/PlaybackInfo", params={"apikey": token}).json()
    local = next(s for s in info["MediaSources"] if s["Protocol"] == "File")
    resp = client.get(
        f"/Videos/{guid}/stream",
        params={"APIKEY": token, "Static": "true", "MediaSourceID": local["Id"]},
    )
    assert resp.status_code == 200


def test_stopped_without_position_marks_played(client: TestClient, seeded: dict) -> None:
    """缺陷#2：Stopped 不带 PositionTicks = 播到结尾，标已看且不清别人的进度。"""
    token = jf_login(client)
    auth = {"ApiKey": token}
    ep = episode_guid(seeded["show"], 2, 1)
    assert (
        client.post(
            "/Sessions/Playing/Stopped", params=auth, json={"ItemId": ep}
        ).status_code
        == 204
    )
    ud = client.get(f"/Items/{ep}", params=auth).json()["UserData"]
    assert ud["Played"] is True and ud["PlaybackPositionTicks"] == 0


def test_progress_zero_clears_resume_point(client: TestClient, seeded: dict) -> None:
    """拖回开头（position=0）要抹掉续播点，已看状态不变。"""
    token = jf_login(client)
    auth = {"ApiKey": token}
    ep = episode_guid(seeded["show"], 1, 1)
    runtime_ticks = 47 * 60 * 1000 * TICKS_PER_MS
    client.post(
        "/Sessions/Playing/Progress",
        params=auth,
        json={"ItemId": ep, "PositionTicks": runtime_ticks // 2},
    )
    assert client.get("/UserItems/Resume", params=auth).json()["TotalRecordCount"] == 1
    client.post(
        "/Sessions/Playing/Progress",
        params=auth,
        json={"ItemId": ep, "PositionTicks": 0},
    )
    assert client.get("/UserItems/Resume", params=auth).json()["TotalRecordCount"] == 0


def test_series_favorite_visible_on_folder(client: TestClient, seeded: dict) -> None:
    """缺陷#7：收藏整剧/整季要在对应条目的 UserData 回显，且不污染 S00E00。"""
    token = jf_login(client)
    auth = {"ApiKey": token}
    show = item_guid(seeded["show"])
    season1 = season_guid(seeded["show"], 1)

    resp = client.post(f"/UserFavoriteItems/{show}", params=auth).json()
    assert resp["IsFavorite"] is True
    assert "UnplayedItemCount" in resp  # 文件夹形态的聚合响应
    assert client.get(f"/Items/{show}", params=auth).json()["UserData"]["IsFavorite"] is True

    assert client.post(f"/UserFavoriteItems/{season1}", params=auth).json()["IsFavorite"] is True
    seasons = client.get(f"/Shows/{show}/Seasons", params=auth).json()["Items"]
    by_index = {s["IndexNumber"]: s for s in seasons}
    assert by_index[1]["UserData"]["IsFavorite"] is True
    assert by_index[2]["UserData"]["IsFavorite"] is False


def test_sort_date_created_without_fields(client: TestClient, seeded: dict) -> None:
    """缺陷#3：sortBy=DateCreated 不带 fields 也必须生效（排序键取自数据）。"""
    token = jf_login(client)
    body = client.get(
        "/Items",
        params={
            "ApiKey": token,
            "parentId": library_guid(seeded["tv_lib"]),
            "includeItemTypes": "Episode",
            "sortBy": "DateCreated",
            "sortOrder": "Descending",
        },
    ).json()
    assert body["TotalRecordCount"] == 3
    # 不带 fields=DateCreated 时 DTO 里没有该字段，但顺序依然确定
    assert "DateCreated" not in body["Items"][0]


def test_users_bad_guid_is_400_not_401(client: TestClient) -> None:
    """终验1#1：非法 userId 是 400，绝不能 401（会触发客户端登录循环）。"""
    token = jf_login(client)
    resp = client.get("/Users/not-a-guid", params={"ApiKey": token})
    assert resp.status_code == 400
    resp = client.get(f"/Users/{'0' * 32}", params={"ApiKey": token})
    assert resp.status_code == 404


def test_emby_prefix_case_insensitive(client: TestClient) -> None:
    """终验1#3：/Emby/... 也要归一化命中。"""
    assert client.get("/Emby/System/Info/Public").status_code == 200


def test_library_view_parent_id_navigable(client: TestClient, seeded: dict) -> None:
    """终验#13：库视图 ParentId 指向根，且拿它回打 /Items 能得到视图列表。"""
    token = jf_login(client)
    views = client.get("/UserViews", params={"ApiKey": token}).json()["Items"]
    parent = views[0]["ParentId"]
    body = client.get("/Items", params={"ApiKey": token, "parentId": parent}).json()
    assert body["TotalRecordCount"] == 2  # 根 → 视图列表


def test_episode_parent_id_gated_by_fields(client: TestClient, seeded: dict) -> None:
    """ParentId 受 fields 门控：不传不出现，传了指向季。"""
    token = jf_login(client)
    ep = episode_guid(seeded["show"], 1, 1)
    plain = client.get(
        "/Items",
        params={"ApiKey": token, "ids": ep},
    ).json()["Items"][0]
    assert "ParentId" not in plain
    with_field = client.get(
        "/Items",
        params={"ApiKey": token, "ids": ep, "fields": "ParentId"},
    ).json()["Items"][0]
    assert with_field["ParentId"] == season_guid(seeded["show"], 1)


def test_nextup_anchor_is_latest_activity(client: TestClient, seeded: dict) -> None:
    """缺陷#5：NextUp 锚定最近活动，而非很久前弃坑的半集。"""
    token = jf_login(client)
    auth = {"ApiKey": token}
    e11 = episode_guid(seeded["show"], 1, 1)
    e12 = episode_guid(seeded["show"], 1, 2)
    runtime_ticks = 47 * 60 * 1000 * TICKS_PER_MS
    # 先在 E01 留半截进度（弃坑），再看完 E02
    client.post(
        "/Sessions/Playing/Progress",
        params=auth,
        json={"ItemId": e11, "PositionTicks": runtime_ticks // 2},
    )
    client.post(f"/UserPlayedItems/{e12}", params=auth)
    nextup = client.get("/Shows/NextUp", params=auth).json()
    # 最近活动是"看完 E02" → NextUp 是 S02E01，不是弃坑的 E01
    assert nextup["Items"][0]["Id"] == episode_guid(seeded["show"], 2, 1)


def test_bad_pagination_params_do_not_500(client: TestClient, seeded: dict) -> None:
    token = jf_login(client)
    resp = client.get(
        "/Items",
        params={
            "ApiKey": token,
            "parentId": library_guid(seeded["movie_lib"]),
            "startIndex": "abc",
            "limit": "xyz",
        },
    )
    assert resp.status_code == 200


def test_default_audio_stream_prefers_default_flag(client: TestClient, seeded: dict) -> None:
    token = jf_login(client)
    guid = item_guid(seeded["movie"])
    info = client.post(f"/Items/{guid}/PlaybackInfo", params={"ApiKey": token}).json()
    local = next(s for s in info["MediaSources"] if s["Protocol"] == "File")
    default_audio = next(
        s for s in local["MediaStreams"] if s["Type"] == "Audio" and s["IsDefault"]
    )
    assert local["DefaultAudioStreamIndex"] == default_audio["Index"]


def test_library_views_have_counts_and_cover(client: TestClient, seeded: dict) -> None:
    """VidHub 实测缺陷：库卡片要有条目计数与封面（ChildCount + ImageTags.Primary）。"""
    token = jf_login(client)
    views = client.get("/UserViews", params={"ApiKey": token}).json()["Items"]
    by_name = {v["Name"]: v for v in views}

    movie_lib = by_name["电影"]
    assert movie_lib["ChildCount"] == 1  # 一部电影
    # 电影库封面 = 最新入库且有海报的条目（盗梦空间）的海报
    assert movie_lib["ImageTags"].get("Primary")

    tv_lib = by_name["剧集"]
    assert tv_lib["ChildCount"] == 1  # 一部剧
    assert tv_lib["RecursiveItemCount"] == 3  # 三集

    # 封面图能真实拉取（图片接口对 LIBRARY GUID 解析同一封面）
    resp = client.get(
        f"/Items/{movie_lib['Id']}/Images/Primary",
        params={"ApiKey": token, "tag": movie_lib["ImageTags"]["Primary"]},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")


def test_items_counts_endpoint(client: TestClient, seeded: dict) -> None:
    """VidHub 实测缺陷：服务器卡片统计来自 GET /Items/Counts。"""
    token = jf_login(client)
    body = client.get("/Items/Counts", params={"ApiKey": token}).json()
    assert body["MovieCount"] == 1
    assert body["SeriesCount"] == 1
    assert body["EpisodeCount"] == 3
    # 12 个计数键全部出现（非可空 int 恒输出）
    for key in (
        "ArtistCount", "ProgramCount", "TrailerCount", "SongCount", "AlbumCount",
        "MusicVideoCount", "BoxSetCount", "BookCount", "ItemCount",
    ):
        assert key in body


def test_library_cover_is_server_rendered_collage(client: TestClient, seeded: dict) -> None:
    """库封面 = 服务端渲染的氛围光货架拼贴，Jellyfin 与控制台双端同一张图。"""
    token = jf_login(client)
    views = client.get("/UserViews", params={"ApiKey": token}).json()["Items"]
    movie_lib = next(v for v in views if v["Name"] == "电影")
    tag = movie_lib["ImageTags"]["Primary"]
    assert len(tag) == 32  # 素材指纹

    # Jellyfin 侧：拼贴图可拉取，21:10 画布
    resp = client.get(f"/Items/{movie_lib['Id']}/Images/Primary", params={"ApiKey": token})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(resp.content))
    assert img.size == (1260, 600)

    # 控制台侧：同一张图（session cookie 认证），ETag 304 协商
    console = client.get(f"/api/v1/libraries/{seeded['movie_lib']}/cover")
    assert console.status_code == 200
    assert console.content == resp.content
    revisit = client.get(
        f"/api/v1/libraries/{seeded['movie_lib']}/cover",
        headers={"If-None-Match": console.headers["ETag"]},
    )
    assert revisit.status_code == 304

    # 无海报素材的库：无 Primary tag、封面 404（前端回退 CSS 货架）
    tv_lib = next(v for v in views if v["Name"] == "剧集")
    assert "Primary" not in tv_lib["ImageTags"]


def test_image_scaling_params(client: TestClient, seeded: dict) -> None:
    """maxWidth 等缩放参数生效（fit-within 只缩不放，缓存复用）。"""
    token = jf_login(client)
    guid = item_guid(seeded["movie"])
    resp = client.get(
        f"/Items/{guid}/Images/Primary", params={"ApiKey": token, "maxWidth": "50"}
    )
    assert resp.status_code == 200
    from io import BytesIO

    from PIL import Image

    img = Image.open(BytesIO(resp.content))
    assert img.width == 50  # 原图 100x150 → 缩到宽 50
    # 不放大：请求超过原尺寸时原样
    big = client.get(
        f"/Items/{guid}/Images/Primary", params={"ApiKey": token, "maxWidth": "4000"}
    )
    assert Image.open(BytesIO(big.content)).width == 100


def test_people_in_item_detail(client: TestClient, seeded: dict) -> None:
    """VidHub 实测缺陷：演职员必须随详情输出（People 字段 + 头像 tag）。"""
    token = jf_login(client)
    guid = item_guid(seeded["movie"])
    people = client.get(f"/Items/{guid}", params={"ApiKey": token}).json()["People"]
    assert [p["Name"] for p in people] == [
        "莱昂纳多·迪卡普里奥",
        "艾伦·佩吉",
        "克里斯托弗·诺兰",
    ]  # 演员按主次序在前，导演在后
    leo = people[0]
    assert leo["Type"] == "Actor" and leo["Role"] == "Cobb"
    assert len(leo["PrimaryImageTag"]) == 32
    assert "PrimaryImageTag" not in people[1]  # 无头像的不给 tag
    assert people[2]["Type"] == "Director" and "Role" not in people[2]

    # 列表接口：不传 fields=People 不输出；传了才有（fields 门控）
    plain = client.get("/Items", params={"ApiKey": token, "ids": guid}).json()["Items"][0]
    assert "People" not in plain
    gated = client.get(
        "/Items", params={"ApiKey": token, "ids": guid, "fields": "People"}
    ).json()["Items"][0]
    assert len(gated["People"]) == 3


def test_list_queries_skip_unused_json_columns(client: TestClient, seeded: dict) -> None:
    """大库列表不应读取 DTO 用不到的演员和媒体流 JSON。"""
    from sqlalchemy import event

    from movieclaw_db.engine import get_database

    token = jf_login(client)
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    engine = get_database().engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        plain = client.get(
            "/Items",
            params={
                "ApiKey": token,
                "parentId": library_guid(seeded["movie_lib"]),
                "includeItemTypes": "Movie",
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert plain.status_code == 200
    sql = "\n".join(statements)
    assert "media_metadata.cast" not in sql
    assert "media_metadata.directors" not in sql
    assert "library_file.audio_streams" not in sql
    assert "library_file.subtitle_streams" not in sql

    statements.clear()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        sources = client.get(
            "/Items",
            params={
                "ApiKey": token,
                "parentId": library_guid(seeded["movie_lib"]),
                "includeItemTypes": "Movie",
                "fields": "MediaSources",
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert sources.status_code == 200
    assert len(sources.json()["Items"][0]["MediaSources"]) == 2
    sql = "\n".join(statements)
    assert "library_file.audio_streams" in sql
    assert "library_file.subtitle_streams" in sql

    statements.clear()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        views = client.get("/UserViews", params={"ApiKey": token})
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert views.status_code == 200
    sql = "\n".join(statements)
    assert "media_metadata.poster_file" in sql
    assert "media_metadata.cast" not in sql
    assert "media_metadata.overview" not in sql


def test_large_library_list_skips_large_json_columns(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    """1,200 部电影携带大 JSON 时，首页仍走最小列集并正确分页。"""
    import json
    import os
    import sqlite3

    from sqlalchemy import event

    from movieclaw_db.engine import get_database

    db_path = os.environ["DATABASE_URL"].split("///", 1)[1]
    timestamp = "2026-08-06 00:00:00.000000"
    large_json = json.dumps(["x" * 2_048])
    item_ids = range(10_000, 11_200)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO media_item
                (id, kind, tmdb_id, title, original_title, aliases, created_at, updated_at)
            VALUES (?, 'movie', ?, ?, ?, '[]', ?, ?)
            """,
            [
                (
                    item_id,
                    item_id,
                    f"Large library item {item_id}",
                    f"Item {item_id}",
                    timestamp,
                    timestamp,
                )
                for item_id in item_ids
            ],
        )
        conn.executemany(
            """
            INSERT INTO media_metadata
                (media_item_id, genres, genre_ids, origin_countries, studios, directors, cast,
                 poster_locked, backdrop_locked, scrape_language, created_at, updated_at)
            VALUES (?, '[]', '[]', ?, ?, ?, ?, 0, 0, '', ?, ?)
            """,
            [
                (item_id, large_json, large_json, large_json, large_json, timestamp, timestamp)
                for item_id in item_ids
            ],
        )
        conn.executemany(
            """
            INSERT INTO library_file
                (library_id, media_item_id, season_number, episode_number, file_path, size_bytes,
                 audio_streams, subtitle_streams, source, created_at, updated_at)
            VALUES (?, ?, 0, 0, ?, 0, ?, ?, 'scanned', ?, ?)
            """,
            [
                (
                    seeded["movie_lib"],
                    item_id,
                    f"/large-media/item-{item_id}.mkv",
                    large_json,
                    large_json,
                    timestamp,
                    timestamp,
                )
                for item_id in item_ids
            ],
        )

    token = jf_login(client)
    statements: list[str] = []
    loaded_bundle_sizes: list[int] = []

    from movieclaw_jellyfin.routes import library as library_routes

    original_load_bundles = library_routes.load_bundles

    async def track_load_bundles(session, item_ids, **kwargs):
        loaded_bundle_sizes.append(len(item_ids))
        return await original_load_bundles(session, item_ids, **kwargs)

    monkeypatch.setattr(library_routes, "load_bundles", track_load_bundles)

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement.lower())

    engine = get_database().engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(
            "/Items",
            params={
                "ApiKey": token,
                "parentId": library_guid(seeded["movie_lib"]),
                "includeItemTypes": "Movie",
                "limit": "20",
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert response.status_code == 200
    body = response.json()
    assert body["TotalRecordCount"] == 1_201
    assert len(body["Items"]) == 20
    # 默认电影库 Items 的 count/page 已在 SQL 完成，不能回退成 1,201 个 bundle。
    assert loaded_bundle_sizes == [20]
    sql = "\n".join(statements)
    assert "media_metadata.cast" not in sql
    assert "media_metadata.directors" not in sql
    assert "media_metadata.origin_countries" not in sql
    assert "library_file.audio_streams" not in sql
    assert "library_file.subtitle_streams" not in sql

    statements.clear()
    loaded_bundle_sizes.clear()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        latest = client.get(
            "/Items/Latest",
            params={
                "ApiKey": token,
                "parentId": library_guid(seeded["movie_lib"]),
                "limit": "20",
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert latest.status_code == 200
    assert len(latest.json()) == 20
    # Latest 先扫描轻量最新单元，再只装载最终的 20 个条目。
    assert loaded_bundle_sizes == [20]
    sql = "\n".join(statements)
    assert "media_metadata.cast" not in sql
    assert "media_metadata.directors" not in sql
    assert "media_metadata.origin_countries" not in sql
    assert "library_file.audio_streams" not in sql
    assert "library_file.subtitle_streams" not in sql


def test_slow_browse_log_is_segmented_and_deidentified(
    client: TestClient, seeded: dict, monkeypatch, caplog
) -> None:
    """部署侧慢日志能定位阶段耗时，且不泄漏媒体标题和路径。"""
    import logging

    from movieclaw_jellyfin.routes import library as library_routes

    monkeypatch.setattr(library_routes, "SLOW_BROWSE_REQUEST_MS", 0)
    caplog.set_level(logging.WARNING, logger="movieclaw_jellyfin.library")
    token = jf_login(client)

    client.get(
        "/Items",
        params={
            "ApiKey": token,
            "parentId": library_guid(seeded["movie_lib"]),
            "includeItemTypes": "Movie",
            "fields": "People,/media/private/token,secret-token",
        },
    )
    client.get("/Items/Latest", params={"ApiKey": token})

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "movieclaw_jellyfin.library"
    ]
    assert any("route=items" in message for message in messages)
    assert any("route=latest" in message for message in messages)
    assert all(
        "candidates=" in message
        and "returned=" in message
        and "load_ms=" in message
        and "build_ms=" in message
        and "encode_ms=" in message
        for message in messages
    )
    assert all("盗梦空间" not in message for message in messages)
    assert all("/media/" not in message for message in messages)
    assert all("secret-token" not in message for message in messages)
    assert any("fields=People,other=2" in message for message in messages)


def test_home_playback_queries_only_load_active_series(
    client: TestClient, seeded: dict, monkeypatch
) -> None:
    """Resume/NextUp 不能为无播放活动的整库条目加载 bundle。"""
    from movieclaw_jellyfin.routes import library as library_routes

    token = jf_login(client)
    auth = {"ApiKey": token}
    episode = episode_guid(seeded["show"], 1, 1)
    runtime_ticks = 47 * 60 * 1000 * TICKS_PER_MS
    assert (
        client.post(
            "/Sessions/Playing/Progress",
            params=auth,
            json={"ItemId": episode, "PositionTicks": runtime_ticks // 2},
        ).status_code
        == 204
    )

    calls: list[list[int]] = []
    original_load_bundles = library_routes.load_bundles

    async def track_load_bundles(session, item_ids, **kwargs):
        calls.append(list(item_ids))
        return await original_load_bundles(session, item_ids, **kwargs)

    monkeypatch.setattr(library_routes, "load_bundles", track_load_bundles)

    resume = client.get("/UserItems/Resume", params=auth).json()
    assert resume["TotalRecordCount"] == 1
    assert resume["Items"][0]["Id"] == episode
    assert calls == [[seeded["show"]]]

    calls.clear()
    next_up = client.get("/Shows/NextUp", params=auth).json()
    assert next_up["TotalRecordCount"] == 1
    assert next_up["Items"][0]["Id"] == episode
    assert calls == [[seeded["show"]]]


def test_person_item_and_filter(client: TestClient, seeded: dict) -> None:
    """点开演员：人物条目可取，personIds 反查参演作品。"""
    from movieclaw_jellyfin.ids import person_guid

    token = jf_login(client)
    pguid = person_guid(seeded["dicaprio"])
    person = client.get(f"/Items/{pguid}", params={"ApiKey": token}).json()
    assert person["Type"] == "Person"
    assert person["Name"] == "莱昂纳多·迪卡普里奥"
    assert person["OriginalTitle"] == "Leonardo DiCaprio"

    body = client.get(
        "/Items",
        params={
            "ApiKey": token,
            "personIds": pguid,
            "recursive": "true",
            "includeItemTypes": "Movie,Series",
        },
    ).json()
    assert body["TotalRecordCount"] == 1
    assert body["Items"][0]["Id"] == item_guid(seeded["movie"])

    # 头像：测试环境图床离线 → 404 优雅降级（生产为图片代理缓存直出）
    avatar = client.get(f"/Items/{pguid}/Images/Primary", params={"ApiKey": token})
    assert avatar.status_code == 404


def test_browse_never_touches_media_files(client: TestClient, seeded: dict) -> None:
    """issue #88 回归：浏览类请求（列表/详情）不碰媒体文件本体。

    把磁盘上的媒体文件全部删掉后再浏览——若代码仍在请求期 stat 文件或
    现读 strm，这里的形态就会变（ETag 现身/消失、strm 源被剔除）。契约：

    - strm 版本**不现读**：Path 保持 .strm 占位路径、Protocol=Http、
      IsRemote=true，真实直链等 PlaybackInfo 播放协商时现读；
    - 本地版本的 ETag 只来自台账 ``file_mtime_ns``（未回填时省略）。
    """
    import os
    import sqlite3
    from pathlib import Path

    token = jf_login(client)
    guid = item_guid(seeded["movie"])

    db_path = os.environ["DATABASE_URL"].split("///", 1)[1]
    with sqlite3.connect(db_path) as conn:
        paths = [r[0] for r in conn.execute("SELECT file_path FROM library_file")]
    for p in paths:
        Path(p).unlink()

    # 全字段单条目：MediaSources 两个版本俱在，strm 未被解析/剔除
    body = client.get(f"/Items/{guid}", params={"ApiKey": token}).json()
    sources = body["MediaSources"]
    assert len(sources) == 2
    remote = next(s for s in sources if s["Protocol"] == "Http")
    assert remote["Path"].endswith(".strm")  # 占位路径，未现读云端直链
    assert remote["IsRemote"] is True
    local = next(s for s in sources if s["Protocol"] == "File")
    assert "ETag" not in local  # mtime 未回填 → 省略，而不是现场 stat

    # 列表带 fields=MediaSources 同样不碰文件
    listing = client.get(
        "/Items",
        params={
            "ApiKey": token,
            "parentId": library_guid(seeded["movie_lib"]),
            "recursive": "true",
            "includeItemTypes": "Movie",
            "fields": "MediaSources",
        },
    ).json()
    assert len(listing["Items"][0]["MediaSources"]) == 2


def test_media_source_etag_comes_from_ledger(client: TestClient, seeded: dict) -> None:
    """ETag 由台账 file_mtime_ns 派生（扫描时落库），请求期零文件系统调用。"""
    import hashlib
    import os
    import sqlite3

    token = jf_login(client)
    guid = item_guid(seeded["movie"])

    db_path = os.environ["DATABASE_URL"].split("///", 1)[1]
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE library_file SET file_mtime_ns = 1723800000123456789 "
            "WHERE file_path LIKE '%.mkv'"
        )
        conn.commit()

    body = client.get(f"/Items/{guid}", params={"ApiKey": token}).json()
    local = next(s for s in body["MediaSources"] if s["Protocol"] == "File")
    assert local["ETag"] == hashlib.md5(b"1723800000123456789").hexdigest()
