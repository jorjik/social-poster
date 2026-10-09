#!/usr/bin/env python3
"""Очередь постов в Buffer (Threads, Bluesky). Только stdlib, без зависимостей.

Ключ берётся из BUFFER_API_KEY (окружение или /root/.hermes/.env), в вывод не попадает.

  buffer_post.py channels [--service threads]
  buffer_post.py queue [--service threads] [--limit 20]
  buffer_post.py add --service threads --text-file post.txt [--due-at 2026-09-28T09:00:00Z]
  buffer_post.py add --service bluesky --text-file post.txt --dry-run

Лимиты символов проверяются до отправки: Threads 500, Bluesky 300 графем, X 280,
LinkedIn 3000.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.request

ENDPOINT = "https://api.buffer.com"
ENV_FILE = "/root/.hermes/.env"
TRACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ig_media_track.json")
LIMITS = {"threads": 500, "bluesky": 300, "x": 280, "linkedin": 3000, "instagram": 2200, "pinterest": 500}

# В Buffer сервис X называется "twitter" - в CLI оставляем привычное "x".
SERVICE_ALIAS = {"x": "twitter"}

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id text dueAt } }
    ... on MutationError { message }
  }
}
"""

CHANNELS = """
query Channels($org: OrganizationId!) {
  channels(input: { organizationId: $org }) { id name service isDisconnected }
}
"""

PINTEREST_BOARDS = """
query PinBoards($org: OrganizationId!) {
  channels(input: { organizationId: $org }) {
    id service
    metadata { ... on PinterestMetadata { boards { serviceId name } } }
  }
}
"""

ORGS = "query { account { organizations { id name } } }"

POSTS = """
query Posts($org: OrganizationId!, $first: Int!) {
  posts(first: $first, input: { organizationId: $org }) {
    edges { node { id text dueAt status externalLink channel { service } } }
  }
}
"""

DELETE_POST = """
mutation DeletePost($input: DeletePostInput!) {
  deletePost(input: $input) {
    ... on DeletePostSuccess { id }
    ... on MutationError { message }
  }
}
"""


def key_var(account: int) -> str:
    return "BUFFER_API_KEY" if account == 1 else f"BUFFER_API_KEY_{account}"


def api_key(account: int = 1) -> str:
    var = key_var(account)
    key = os.environ.get(var, "").strip()
    if key:
        return key
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(var + "="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except FileNotFoundError:
        pass
    sys.exit(f"{var} не найден ни в окружении, ни в {ENV_FILE}")


_ENV_CACHE: dict = {}


def env_value(var: str) -> str:
    """Значение из окружения или ENV_FILE (кэшируется в модуле)."""
    if not _ENV_CACHE:
        try:
            with open(ENV_FILE, encoding="utf-8") as fh:
                for line in fh:
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, v = line.split("=", 1)
                        _ENV_CACHE[k.strip()] = v.strip().strip("'\"")
        except FileNotFoundError:
            pass
    return os.environ.get(var, "") or _ENV_CACHE.get(var, "")


def delete_wp_media(url: str, wp_user: str, wp_pw: str) -> bool:
    """Удаляет медиафайл из WP по его публичному URL. True = удалён."""
    import base64
    import subprocess
    auth = base64.b64encode(f"{wp_user}:{wp_pw}".encode()).decode()
    slug = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # найти id медиа по slug
    r2 = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Basic {auth}",
         "-G", "--data-urlencode", f"slug={slug}",
         "https://master-jenya.com/wp-json/wp/v2/media"],
        capture_output=True, text=True, timeout=60,
    )
    try:
        items = json.loads(r2.stdout)
        if items and items[0].get("id"):
            mid = items[0]["id"]
            r3 = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "DELETE",
                 f"https://master-jenya.com/wp-json/wp/v2/media/{mid}?force=true",
                 "-H", f"Authorization: Basic {auth}"],
                capture_output=True, text=True, timeout=60,
            )
            return r3.stdout.strip() == "200"
    except (json.JSONDecodeError, KeyError):
        pass
    return False


def gql(query: str, variables: dict | None = None, account: int = 1) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key(account)}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:400]}")
    if payload.get("errors"):
        sys.exit("GraphQL: " + json.dumps(payload["errors"], ensure_ascii=False)[:600])
    return payload["data"]


def graphemes(text: str) -> int:
    """Приближение к графемам Bluesky: диакритика, ZWJ и модификаторы тона не считаются."""
    total = 0
    for ch in text:
        if unicodedata.combining(ch) or ch in "\u200d\ufe0e\ufe0f":
            continue
        if 0x1F3FB <= ord(ch) <= 0x1F3FF:
            continue
        total += 1
    return total


def org_id(account: int = 1) -> str:
    orgs = gql(ORGS, account=account)["account"]["organizations"]
    if not orgs:
        sys.exit(f"В аккаунте Buffer #{account} нет ни одной организации")
    return orgs[0]["id"]


def all_channels(account: int = 1) -> list[dict]:
    return gql(CHANNELS, {"org": org_id(account)}, account=account)["channels"]


def pick_channel(service: str, account: int = 1) -> str:
    api_service = SERVICE_ALIAS.get(service, service)
    matches = [c for c in all_channels(account) if c["service"] == api_service]
    if not matches:
        have = ", ".join(sorted({c["service"] for c in all_channels(account)})) or "нет ни одного"
        sys.exit(f"Канал {service} не подключён к Buffer #{account}. Подключены: {have}")
    if len(matches) > 1:
        names = ", ".join(f"{c['name']} ({c['id']})" for c in matches)
        sys.exit(f"Каналов {service} несколько, оставь один или передай id вручную: {names}")
    return matches[0]["id"]


def cmd_channels(args: argparse.Namespace) -> None:
    wanted = SERVICE_ALIAS.get(args.service, args.service) if args.service else None
    for acc in [args.account] if args.account else [1, 2]:
        try:
            api_key(acc)
        except SystemExit:
            continue
        print(f"=== Buffer #{acc} ===")
        for ch in all_channels(acc):
            if wanted and ch["service"] != wanted:
                continue
            flag = " (отключён)" if ch["isDisconnected"] else ""
            print(f"{ch['service']:<12} {ch['name']}{flag}\n  id: {ch['id']}")


def cmd_queue(args: argparse.Namespace) -> None:
    account = args.account or 1
    edges = gql(POSTS, {"org": org_id(account), "first": args.limit}, account=account)["posts"]["edges"]
    rows = [e["node"] for e in edges]
    if args.service:
        wanted = SERVICE_ALIAS.get(args.service, args.service)
        rows = [r for r in rows if (r.get("channel") or {}).get("service") == wanted]
    if not rows:
        print("Очередь пуста")
        return
    for row in rows:
        text = " ".join((row.get("text") or "").split())
        link = row.get("externalLink") or ""
        print(f"[{row['status']}] {row.get('dueAt') or '-'} — {text[:110]}")
        if link:
            print(f"    {link}")


def cmd_add(args: argparse.Namespace) -> None:
    account = args.account or 1
    with open(args.text_file, encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text:
        sys.exit("Файл с текстом пуст")

    limit = LIMITS.get(args.service)
    size = graphemes(text)
    if args.service == "threads":
        size_bytes = len(text.encode("utf-8"))
        if size_bytes > 500:
            sys.exit(f"threads: {size_bytes} UTF-8 байт при лимите 500 — сократи текст")
    if limit and size > limit:
        hint = " (у X Premium лимит выше, но пост всё равно видно только в первых 280 символах)" if args.service == "x" else ""
        sys.exit(f"{args.service}: {size} символов при лимите {limit} — сократи текст{hint}")
    if not args.allow_no_link:
        required_links = ["t.me/SmartFavoriteBot", "evgeniy.in.ua", "master-jenya"]
        if not any(l in text for l in required_links):
            sys.exit("В тексте нет ссылки на продвигаемый ресурс (обойти: --allow-no-link)")

    payload = {
        "channelId": args.channel_id or "<id канала из buffer_post.py channels>",
        "text": text,
        "schedulingType": "automatic",
        "mode": "customScheduled" if args.due_at else "addToQueue",
        "source": "hermes-cron",
    }
    if args.service == "instagram":
        if not args.image_url:
            sys.exit("instagram требует --image-url (публичный URL картинки)")
        payload["assets"] = [{"image": {"url": args.image_url}}]
        payload["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}
    if args.service == "pinterest":
        if not args.image_url:
            sys.exit("pinterest требует --image-url (публичный URL картинки)")
        payload["assets"] = [{"image": {"url": args.image_url}}]
        # title берём из первой строки текста; борд — первый в канале
        title = text.splitlines()[0].strip()[:100] if text else ""
        board_id = args.pin_board_id
        if not board_id:
            data = gql(PINTEREST_BOARDS, {"org": org_id(account)}, account=account)
            for ch in data["channels"]:
                if ch["service"] == "pinterest":
                    boards = (ch.get("metadata") or {}).get("boards") or []
                    if not boards:
                        sys.exit("У pinterest-канала нет ни одного борда — создай его в Buffer UI")
                    board_id = boards[0]["serviceId"]
                    break
            else:
                sys.exit("pinterest-канал не найден в аккаунте")
        payload["metadata"] = {"pinterest": {"boardServiceId": board_id, "title": title, "url": "https://surl.li/mmlnjb"}}
    if args.due_at:
        payload["dueAt"] = args.due_at
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"— {size}/{limit or '?'} символов, ссылка на месте, не отправлено (--dry-run)")
        return

    payload["channelId"] = args.channel_id or pick_channel(args.service, account)

    result = gql(CREATE_POST, {"input": payload}, account=account)["createPost"]
    if "message" in result:
        sys.exit(f"Buffer отказал: {result['message']}")
    post = result["post"]
    print(f"В очереди: id={post['id']} dueAt={post.get('dueAt') or 'по расписанию канала'} ({size} символов)")

    # трекинг медиа: IG/Pinterest-картинки на master-jenya.com удалим после публикации
    if args.service in ("instagram", "pinterest") and args.image_url \
            and "master-jenya.com/wp-content/uploads" in args.image_url:
        track = {}
        try:
            with open(TRACK_FILE, encoding="utf-8") as fh:
                track = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        track[post["id"]] = {"url": args.image_url, "service": args.service, "account": account}
        with open(TRACK_FILE, "w", encoding="utf-8") as fh:
            json.dump(track, fh, ensure_ascii=False, indent=2)


def cmd_cleanup_ig(args: argparse.Namespace) -> None:
    """Удаляет IG/Pinterest-картинки из WP, чьи посты уже опубликованы в Buffer."""
    try:
        with open(TRACK_FILE, encoding="utf-8") as fh:
            track = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Трек-файла нет — нечего чистить")
        return

    # статусы постов по аккаунтам
    statuses = {}
    for acc in sorted({v["account"] for v in track.values()}):
        edges = gql(POSTS, {"org": org_id(acc), "first": 50}, account=acc)["posts"]["edges"]
        statuses.update({e["node"]["id"]: e["node"]["status"] for e in edges})

    wp_user = env_value("WP_USER")
    wp_pw = env_value("WP_APP_PASSWORD")
    if not wp_user or not wp_pw:
        sys.exit("WP_USER/WP_APP_PASSWORD не найдены")

    kept = {}
    for post_id, meta in track.items():
        status = statuses.get(post_id)
        if status == "sent":
            removed = delete_wp_media(meta["url"], wp_user, wp_pw)
            print(f"{post_id} ({meta['service']}): {status} — медиа {'удалено' if removed else 'пропущено'}")
        else:
            kept[post_id] = meta
            print(f"{post_id} ({meta['service']}): {status or 'не найден'} — оставляем")
    with open(TRACK_FILE, "w", encoding="utf-8") as fh:
        json.dump(kept, fh, ensure_ascii=False, indent=2)


def cmd_delete(args: argparse.Namespace) -> None:
    """Удаляет посты из очереди по id. Опубликованные Buffer не отдаёт."""
    for post_id in args.post_ids:
        result = gql(DELETE_POST, {"input": {"id": post_id}}, account=args.account)["deletePost"]
        if "id" in result:
            print(f"Удалён: id={result['id']}")
        else:
            print(f"НЕ удалён: id={post_id} — {result.get('message', 'без ответа')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Очередь постов Buffer (несколько аккаунтов)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_account(p: argparse.ArgumentParser) -> None:
        p.add_argument("--account", type=int, choices=[1, 2], help="1 = BUFFER_API_KEY (по умолч.), 2 = BUFFER_API_KEY_2")

    p_ch = sub.add_parser("channels", help="показать подключённые каналы (без --account — оба аккаунта)")
    p_ch.add_argument("--service")
    add_account(p_ch)
    p_ch.set_defaults(func=cmd_channels)

    p_q = sub.add_parser("queue", help="показать очередь и последние посты")
    p_q.add_argument("--service")
    p_q.add_argument("--limit", type=int, default=20)
    add_account(p_q)
    p_q.set_defaults(func=cmd_queue)

    p_a = sub.add_parser("add", help="положить пост в очередь")
    p_a.add_argument("--service", required=True, choices=sorted(LIMITS))
    p_a.add_argument("--text-file", required=True)
    p_a.add_argument("--due-at", help="ISO 8601 UTC, иначе слот канала по умолчанию")
    p_a.add_argument("--dry-run", action="store_true")
    p_a.add_argument("--allow-no-link", action="store_true")
    p_a.add_argument("--channel-id", help="если каналов с этим service несколько")
    p_a.add_argument("--image-url", help="обязательно для instagram/pinterest: публичный URL картинки")
    p_a.add_argument("--pin-board-id", help="pinterest: serviceId борда (по умолчанию — первый борд канала)")
    add_account(p_a)
    p_a.set_defaults(func=cmd_add)

    p_d = sub.add_parser("delete", help="удалить посты из очереди по id (опубликованные не удаляются)")
    p_d.add_argument("post_ids", nargs="+", help="id постов из вывода queue")
    add_account(p_d)
    p_d.set_defaults(func=cmd_delete)

    p_c = sub.add_parser("cleanup-ig", help="удалить IG/Pinterest-картинки из WP, чьи посты уже опубликованы")
    p_c.set_defaults(func=cmd_cleanup_ig)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
