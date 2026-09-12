"""Synthetic subprocess fixture, deliberately incapable of invoking a model or tools."""
import json
import sys
import time


def main():
    request = json.load(sys.stdin)
    mode = request.get("fixture_mode", "normal")
    if mode == "timeout":
        time.sleep(60)
    if mode == "crash":
        raise SystemExit(17)
    if mode == "invalid_json":
        print("{incomplete")
        return
    request_id = request["request_id"]
    result = {"request_id": request_id, "native_status": "success"}
    if request["purpose"] == "prepare":
        result.update(has_topic=bool(request.get("own_material")), own_summary="Synthetic topic" if request.get("own_material") else "")
    else:
        closing = request.get("close", False)
        result.update(reply_to=request.get("reply_to"), reply="今天的測試聊完了。" if closing else "這是獨立測試人格的發言。",
                      move="closure" if closing else "affect", wants_reply=not closing,
                      digest_candidate={"summary": "分享了一則測試話題。", "shared_message_ids": [request_id], "disagreement": ""})
    if mode == "wrong_id":
        result["request_id"] = "unrelated-request"
    if mode == "error_result":
        result["native_status"] = "error"
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
