from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from bson import ObjectId
import bcrypt
import jwt
import os
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ── Config (from .env) ───────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI")
JWT_SECRET = os.getenv("JWT_SECRET", "fallback_secret")


# ── MongoDB ──────────────────────────────────────────────
client  = MongoClient(MONGO_URI)
db      = client["studentpulse"]
admins  = db["admins"]
surveys = db["surveys"]

# ── Seed admin on first run ──────────────────────────────
def seed_admin():
    if not admins.find_one({"username": "admin"}):
        hashed = bcrypt.hashpw(b"admin123", bcrypt.gensalt())
        admins.insert_one({"username": "admin", "password": hashed})
        print("Admin seeded  username=admin  password=admin123")

seed_admin()

# ── JWT decorator ────────────────────────────────────────
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "No token"}), 401
        token = auth.split(" ", 1)[1]
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.admin = data
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except Exception:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated

def oid_to_str(doc):
    if doc is None:
        return None
    doc["_id"] = str(doc["_id"])
    if "submittedAt" in doc and isinstance(doc["submittedAt"], datetime):
        doc["submittedAt"] = doc["submittedAt"].isoformat()
    return doc

# ── Routes ───────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    body     = request.get_json(force=True) or {}
    username = body.get("username", "")
    password = body.get("password", "")
    admin = admins.find_one({"username": username})
    if not admin:
        return jsonify({"error": "Invalid credentials"}), 401
    if not bcrypt.checkpw(password.encode(), admin["password"]):
        return jsonify({"error": "Invalid credentials"}), 401
    token = jwt.encode(
        {"id": str(admin["_id"]), "username": username,
         "exp": datetime.utcnow() + timedelta(hours=24)},
        JWT_SECRET, algorithm="HS256"
    )
    return jsonify({"token": token, "username": username})

@app.route("/api/survey", methods=["POST"])
def submit_survey():
    data = request.get_json(force=True) or {}
    data["submittedAt"] = datetime.utcnow()
    for field in ["enjoyLearning","facultyInfluence","peerPressureMotivation",
                  "mentoringImportance","educationCareerImportance",
                  "preparednessRating","recommendUniversity","overallExperience"]:
        if field in data:
            try: data[field] = int(data[field])
            except: pass
    result = surveys.insert_one(data)
    return jsonify({"success": True, "id": str(result.inserted_id)})

@app.route("/api/admin/surveys", methods=["GET"])
@token_required
def get_surveys():
    docs = list(surveys.find().sort("submittedAt", -1))
    return jsonify([oid_to_str(d) for d in docs])

@app.route("/api/admin/stats", methods=["GET"])
@token_required
def get_stats():
    total = surveys.count_documents({})
    branch_agg = list(surveys.aggregate([{"$group": {"_id": "$branch", "count": {"$sum": 1}}}]))
    avg_result = list(surveys.aggregate([{"$group": {
        "_id": None,
        "avgEnjoyLearning": {"$avg": "$enjoyLearning"},
        "avgOverallExp":    {"$avg": "$overallExperience"},
        "avgPreparedness":  {"$avg": "$preparednessRating"},
        "avgMentoring":     {"$avg": "$mentoringImportance"},
        "avgRecommend":     {"$avg": "$recommendUniversity"},
    }}]))
    avg_ratings = avg_result[0] if avg_result else {}
    avg_ratings.pop("_id", None)
    internship    = list(surveys.aggregate([{"$group": {"_id": "$internshipParticipated", "count": {"$sum": 1}}}]))
    self_learning = list(surveys.aggregate([{"$group": {"_id": "$selfLearning", "count": {"$sum": 1}}}]))
    recent = [oid_to_str(d) for d in surveys.find(
        {}, {"name":1,"branch":1,"submittedAt":1,"overallExperience":1}
    ).sort("submittedAt",-1).limit(5)]
    return jsonify({"total":total,"branchAgg":branch_agg,"avgRatings":avg_ratings,
                    "internship":internship,"selfLearning":self_learning,"recent":recent})

@app.route("/api/admin/surveys/<survey_id>", methods=["DELETE"])
@token_required
def delete_survey(survey_id):
    try:
        surveys.delete_one({"_id": ObjectId(survey_id)})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    print("Student Pulse running on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
