import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit_log import get_log, log_entry
from labels import make_label
from signals import combine_scores, get_llm_signal, get_stylometric_signal
from store import get_submission, save_submission, update_status

app = Flask(__name__)

# Rate limiting keyed by client IP. With no auth in scope, creator_id is spoofable and
# useless for abuse prevention, so IP is the meaningful throttle key (see planning.md
# Section 8). Limits chosen: a real writer rarely submits more than a handful of pieces a
# minute, while a flooding script trips the ceiling immediately.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    signal1 = get_llm_signal(text)
    signal2 = get_stylometric_signal(text)
    combined = combine_scores(signal1, signal2)

    confidence = combined["combined_score"]
    attribution = combined["attribution"]
    label = make_label(attribution, confidence)

    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "timestamp": timestamp,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "llm_score": signal1["ai_likelihood"],
        "llm_rationale": signal1["rationale"],
        "stylometric_score": signal2["ai_likelihood"],
        "stylometric_features": signal2["features"],
        "appeal_filed": False,
        "status": "classified",
    }

    save_submission(record)
    log_entry("submission", content_id, record)

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence": confidence,
            "label": label,
            "signals": {
                "llm": {
                    "ai_likelihood": signal1["ai_likelihood"],
                    "rationale": signal1["rationale"],
                },
                "stylometric": {
                    "ai_likelihood": signal2["ai_likelihood"],
                    "features": signal2["features"],
                },
            },
            "status": "classified",
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not creator_reasoning:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    original = get_submission(content_id)
    if original is None:
        return jsonify({"error": f"no submission found for content_id {content_id}"}), 404

    appeal_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # Update the stored submission's status; the original decision is preserved (no
    # automated re-classification, per planning.md Section 6).
    update_status(content_id, "under_review", extra={"appeal_filed": True})

    # Append an appeal entry to the audit log with a snapshot of the original decision so a
    # reviewer sees both side by side.
    appeal_record = {
        "appeal_id": appeal_id,
        "content_id": content_id,
        "creator_id": original.get("creator_id"),
        "timestamp": timestamp,
        "appeal_reasoning": creator_reasoning,
        "status": "under_review",
        "original_decision": {
            "attribution": original.get("attribution"),
            "confidence": original.get("confidence"),
            "label": original.get("label"),
            "llm_score": original.get("llm_score"),
            "stylometric_score": original.get("stylometric_score"),
        },
    }
    log_entry("appeal", content_id, appeal_record)

    return jsonify(
        {
            "appeal_id": appeal_id,
            "content_id": content_id,
            "status": "under_review",
            "message": "Appeal received. This content is now under review.",
        }
    )


@app.route("/appeals", methods=["GET"])
def appeals():
    entries = [e for e in get_log() if e.get("entry_type") == "appeal"]
    return jsonify({"appeals": entries})


@app.route("/submissions/<content_id>", methods=["GET"])
def submission(content_id):
    record = get_submission(content_id)
    if record is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(record)


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True)
