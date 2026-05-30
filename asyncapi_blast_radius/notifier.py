import json
import os
from urllib import error, request

from asyncapi_blast_radius.config import load_local_env


load_local_env()


def send_slack_notification(message: str) -> bool:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("Slack webhook not configured. Skipping notification.")
        return False

    payload = json.dumps({"text": message}).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                print("Slack notification failed.")
                return False
    except error.URLError as exc:
        print(f"Slack notification failed: {exc}")
        return False

    print("Slack notification sent.")
    return True
