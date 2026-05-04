
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS
import sqlite3, os, threading, subprocess, sys

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR   = os.path.join(BASE_DIR, 'templates')
DB_PATH        = os.path.join(BASE_DIR, 'violations.db')
VIOLATIONS_DIR = os.path.join(BASE_DIR, 'violations')
UPLOADS_DIR    = os.path.join(BASE_DIR, 'uploads')

os.makedirs(VIOLATIONS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR,    exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR)
CORS(app)

_status = {"running": False, "video": "", "error": ""}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Dashboard page 
@app.route('/')
def home():
    return render_template('dashboard.html')


# ── Upload video + start processing 
@app.route('/upload', methods=['POST'])
def upload_video():
    if 'video' not in request.files:
        return jsonify({"error": "No video file sent"}), 400
    f = request.files['video']
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    if _status["running"]:
        return jsonify({"error": "Already processing a video, please wait"}), 409

    save_path = os.path.join(UPLOADS_DIR, 'current_video.mkv')
    f.save(save_path)

    def run():
        _status["running"] = True
        _status["video"]   = f.filename
        _status["error"]   = ""
        try:
            main_py = os.path.join(BASE_DIR, 'main.py')
            result  = subprocess.run(
                [sys.executable, main_py, save_path],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                _status["error"] = (result.stderr or "Unknown error")[-600:]
        except Exception as e:
            _status["error"] = str(e)
        finally:
            _status["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"message": f"Processing started: {f.filename}"})


# ── Processing status ─
@app.route('/status')
def get_status():
    return jsonify(_status)


# ── Violations list
@app.route('/violations', methods=['GET'])
def get_violations():
    vtype     = request.args.get('type',      '').strip()
    violation = request.args.get('violation', '').strip()
    limit     = min(int(request.args.get('limit',  500)), 2000)
    offset    = int(request.args.get('offset', 0))

    sql    = "SELECT * FROM violations WHERE 1=1"
    params = []
    if vtype:
        sql += " AND type = ?"
        params.append(vtype)
    if violation:
        sql += " AND violation LIKE ?"
        params.append(f"%{violation}%")
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    try:
        conn = get_db()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Stats 
@app.route('/stats')
def get_stats():
    try:
        conn = get_db()
        def q(sql): return conn.execute(sql).fetchone()[0]
        data = {
            "total":     q("SELECT COUNT(*) FROM violations"),
            "overspeed": q("SELECT COUNT(*) FROM violations WHERE violation LIKE '%OverSpeed%'"),
            "no_helmet": q("SELECT COUNT(*) FROM violations WHERE violation LIKE '%No Helmet%'"),
            "cars":      q("SELECT COUNT(*) FROM violations WHERE type='car'"),
            "bikes":     q("SELECT COUNT(*) FROM violations WHERE type='bike'"),
            "avg_speed": round(q("SELECT COALESCE(AVG(speed),0) FROM violations"), 1),
        }
        conn.close()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Serve evidence image 
@app.route('/image/<path:filename>')
def get_image(filename):
    # strip any directory traversal attempts
    filename = os.path.basename(filename)
    path = os.path.join(VIOLATIONS_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    return send_file(path, mimetype='image/jpeg')


# ── Clear all violations 
@app.route('/violations', methods=['DELETE'])
def clear_violations():
    try:
        conn = get_db()
        conn.execute("DELETE FROM violations")
        conn.commit()
        conn.close()
        return jsonify({"message": "Cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000, threaded=True)
