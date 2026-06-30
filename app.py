import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from audit_log import get_log, log_entry
from signals import get_llm_signal

app = Flask(__name__)


def _label_for_score(score):
    if score >= 0.70:
        return "likely_ai"
    if score <= 0.30:
        return "likely_human"
    return "uncertain"


@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    content_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    signal1 = get_llm_signal(text)
    llm_score = signal1["ai_likelihood"]

    # Placeholder: confidence is signal 1's score alone until Milestone 4 combines
    # it with signal 2 into a single weighted confidence score.
    confidence = llm_score
    attribution = _label_for_score(confidence)

    log_entry(
        "submission",
        content_id,
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "attribution": attribution,
            "confidence": confidence,
            "llm_score": llm_score,
            "llm_rationale": signal1["rationale"],
            "status": "classified",
        },
    )

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence": confidence,
            "label": f"(placeholder, signal 1 only) {attribution}",
            "status": "classified",
        }
    )


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": get_log()})


if __name__ == "__main__":
    app.run(debug=True)
