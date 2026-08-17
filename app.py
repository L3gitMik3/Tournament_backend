from flask import Flask, request, jsonify
from flask_cors import CORS
import pymysql
import pymysql.cursors
import os
import jwt
import base64
import requests
from datetime import datetime, timedelta, date, time
from functools import wraps
from dotenv import load_dotenv

load_dotenv()

# ================================================================
# APP SETUP
# ================================================================
app = Flask(__name__)

# CORS - Allow your frontend domains
CORS(app, origins=[
    'https://kaizen-cup.vercel.app',
    'https://michaelhyrax.alwaysdata.net',
    'http://localhost:3000',
    'http://localhost:5173'
])

app.config['UPLOAD_FOLDER'] = 'static/images'

# ================================================================
# CONFIGURATION - From Environment Variables
# ================================================================
DB_HOST = os.getenv('DB_HOST', 'mysql-michaelhyrax.alwaysdata.net')
DB_USER = os.getenv('DB_USER', 'michaelhyrax')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'modcom2026')
DB_NAME = os.getenv('DB_NAME', 'michaelhyrax_kaizen_cup')

JWT_SECRET = os.getenv('JWT_SECRET', 'kaizen-cup-secret-key-2026')
JWT_EXPIRY_HOURS = int(os.getenv('JWT_EXPIRY_HOURS', 24))
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'kaizencup2026@bucksacademy')

# ================================================================
# DATABASE CONNECTION
# ================================================================
def get_db():
    """Get a database connection"""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

# ================================================================
# HELPER FUNCTIONS
# ================================================================
def safe_row(row):
    """Convert date/time objects to strings for JSON"""
    if not row:
        return row
    for key, val in row.items():
        if isinstance(val, (date, datetime)):
            row[key] = val.isoformat()
        elif isinstance(val, time):
            row[key] = str(val)
        elif isinstance(val, timedelta):
            row[key] = str(val)
    return row

def token_required(f):
    """JWT token decorator for protected routes"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        if not token:
            return jsonify({"error": "Token is missing"}), 401
        try:
            jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated

# ================================================================
# HEALTH CHECK
# ================================================================
@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "message": "Kaizen Cup API is running"}), 200

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "message": "Kaizen Cup API",
        "version": "1.0.0",
        "endpoints": {
            "matches": "/api/matches",
            "standings": "/api/standings",
            "gallery": "/api/gallery",
            "admin": "/api/admin/matches",
            "login": "/api/admin/login",
            "register": "/api/kaizen"
        }
    }), 200

# ================================================================
# ADMIN AUTHENTICATION
# ================================================================
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    """Admin login - returns JWT token"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    password = data.get('password', '')
    
    if password != ADMIN_PASSWORD:
        return jsonify({"error": "Invalid credentials"}), 401
    
    token = jwt.encode(
        {
            "identity": "admin",
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
        },
        JWT_SECRET,
        algorithm="HS256"
    )
    return jsonify({
        "access_token": token,
        "message": "Login successful"
    }), 200

# ================================================================
# MATCHES - PUBLIC (For Frontend)
# ================================================================
@app.route('/api/matches', methods=['GET'])
def get_matches():
    """Get all matches with optional filters"""
    age_category = request.args.get('age_category')
    group_name = request.args.get('group_name')
    status = request.args.get('status')
    
    sql = "SELECT * FROM matches WHERE 1=1"
    params = []
    
    if age_category:
        sql += " AND age_category = %s"
        params.append(age_category)
    if group_name:
        sql += " AND group_name = %s"
        params.append(group_name)
    if status:
        sql += " AND status = %s"
        params.append(status)
    
    sql += " ORDER BY match_date, match_time"
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [safe_row(r) for r in cur.fetchall()]
        
        response = jsonify(rows)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/matches/today', methods=['GET'])
def get_today_matches():
    """Get today's matches"""
    today = date.today().isoformat()
    age_category = request.args.get('age_category')
    
    sql = "SELECT * FROM matches WHERE match_date = %s"
    params = [today]
    
    if age_category:
        sql += " AND age_category = %s"
        params.append(age_category)
    
    sql += " ORDER BY match_time"
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [safe_row(r) for r in cur.fetchall()]
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/matches/<int:match_id>', methods=['GET'])
def get_match(match_id):
    """Get a single match by ID"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM matches WHERE id = %s", (match_id,))
            match = cur.fetchone()
            if not match:
                return jsonify({"error": "Match not found"}), 404
            return jsonify(safe_row(match)), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# STANDINGS - Frontend calculates from this data
# ================================================================
@app.route('/api/standings', methods=['GET'])
def get_standings():
    """Get finished matches for standings calculation"""
    age_category = request.args.get('age')
    group_name = request.args.get('group')
    
    sql = "SELECT * FROM matches WHERE status = 'finished'"
    params = []
    
    if age_category:
        sql += " AND age_category = %s"
        params.append(age_category)
    if group_name:
        sql += " AND group_name = %s"
        params.append(group_name)
    
    sql += " ORDER BY match_date, match_time"
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [safe_row(r) for r in cur.fetchall()]
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# MATCHES - ADMIN (Protected)
# ================================================================
@app.route('/api/admin/matches', methods=['POST'])
@token_required
def create_match():
    """Create a new match"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    required = ['team_1', 'team_2', 'age_category', 'match_date', 'match_time', 'venue']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO matches 
                (team_1, team_2, age_category, group_name, match_date, match_time, venue, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')
            """, (
                data['team_1'],
                data['team_2'],
                data['age_category'],
                data.get('group_name'),
                data['match_date'],
                data['match_time'],
                data['venue']
            ))
            conn.commit()
            new_id = cur.lastrowid
            cur.execute("SELECT * FROM matches WHERE id = %s", (new_id,))
            match = safe_row(cur.fetchone())
        return jsonify(match), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/matches/<int:match_id>', methods=['PUT'])
@token_required
def update_match(match_id):
    """Update match details or scores"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    allowed_fields = ['team_1', 'team_2', 'age_category', 'group_name', 
                     'match_date', 'match_time', 'venue', 'status', 
                     'score_1', 'score_2']
    
    updates = []
    params = []
    
    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = %s")
            params.append(data[field])
    
    if not updates:
        return jsonify({"error": "No fields to update"}), 400
    
    params.append(match_id)
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM matches WHERE id = %s", (match_id,))
            if not cur.fetchone():
                return jsonify({"error": "Match not found"}), 404
            
            cur.execute(
                f"UPDATE matches SET {', '.join(updates)} WHERE id = %s",
                params
            )
            conn.commit()
            
            cur.execute("SELECT * FROM matches WHERE id = %s", (match_id,))
            match = safe_row(cur.fetchone())
        return jsonify(match), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/matches/<int:match_id>/complete', methods=['POST'])
@token_required
def complete_match(match_id):
    """Complete a match with final scores"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    if 'score_1' not in data or 'score_2' not in data:
        return jsonify({"error": "Both scores are required"}), 400
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE matches 
                SET score_1 = %s, score_2 = %s, status = 'finished'
                WHERE id = %s
            """, (data['score_1'], data['score_2'], match_id))
            conn.commit()
            
            cur.execute("SELECT * FROM matches WHERE id = %s", (match_id,))
            match = safe_row(cur.fetchone())
        return jsonify({
            "message": "Match completed successfully",
            "match": match
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/matches/<int:match_id>', methods=['DELETE'])
@token_required
def delete_match(match_id):
    """Delete a match"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, team_1, team_2 FROM matches WHERE id = %s", (match_id,))
            match = cur.fetchone()
            
            if not match:
                return jsonify({"error": "Match not found"}), 404
            
            cur.execute("DELETE FROM matches WHERE id = %s", (match_id,))
            conn.commit()
            
            return jsonify({
                "message": "Match deleted successfully",
                "id": match_id,
                "team_1": match['team_1'],
                "team_2": match['team_2']
            }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# TEAM REGISTRATION
# ================================================================
@app.route('/api/kaizen', methods=['POST'])
def add_team_registration():
    """Register a team for the tournament"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ['team_name', 'age_category', 'manager_name', 'phone', 'town']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400

    agreed_to_terms = 1 if data.get('agreed_to_terms') is True else 0
    players = data.get('players', None)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            sql = """INSERT INTO team_registrations
                     (team_name, age_category, manager_name, phone, town, players, agreed_to_terms)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)"""
            cur.execute(sql, (
                data['team_name'], data['age_category'], data['manager_name'],
                data['phone'], data['town'], players, agreed_to_terms
            ))
            conn.commit()
        return jsonify({"success": "Registration added successfully"}), 201
    except Exception as e:
        return jsonify({"error": "Database error", "details": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/registrations', methods=['GET'])
@token_required
def get_all_registrations():
    """Get all team registrations (admin only)"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, team_name, age_category, manager_name, phone, town, created_at
                FROM team_registrations
                ORDER BY id DESC
            """)
            rows = [safe_row(r) for r in cur.fetchall()]
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# TOURNAMENT GALLERY
# ================================================================
@app.route('/api/gallery', methods=['GET'])
def get_gallery():
    """Get gallery for the current tournament"""
    tournament = request.args.get('tournament', 'Kaizen Cup 2026')
    category = request.args.get('category')
    
    sql = "SELECT id, image_title, image_url, category FROM tournament_gallery WHERE tournament_name = %s"
    params = [tournament]
    
    if category:
        sql += " AND category = %s"
        params.append(category)
    
    sql += " ORDER BY display_order, created_at DESC"
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [safe_row(r) for r in cur.fetchall()]
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/gallery/add', methods=['POST'])
@token_required
def add_gallery_image():
    """Add an image to the gallery (admin only)"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    required = ['image_title', 'image_url']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_gallery 
                (tournament_name, image_title, image_url, category, display_order)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                data.get('tournament_name', 'Kaizen Cup 2026'),
                data['image_title'],
                data['image_url'],
                data.get('category', 'General'),
                data.get('display_order', 0)
            ))
            conn.commit()
            new_id = cur.lastrowid
            cur.execute("SELECT * FROM tournament_gallery WHERE id = %s", (new_id,))
            image = safe_row(cur.fetchone())
        return jsonify(image), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/gallery/<int:image_id>', methods=['DELETE'])
@token_required
def delete_gallery_image(image_id):
    """Delete a gallery image (admin only)"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM tournament_gallery WHERE id = %s", (image_id,))
            if not cur.fetchone():
                return jsonify({"error": "Image not found"}), 404
            
            cur.execute("DELETE FROM tournament_gallery WHERE id = %s", (image_id,))
            conn.commit()
        return jsonify({"message": "Image deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# SOKO GARDEN - AUTH (Optional - Keep if needed)
# ================================================================
@app.route('/api/signup', methods=['POST'])
def signup():
    """User signup (for Soko Garden)"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ['username', 'email', 'password', 'phone']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, email, password, phone) VALUES (%s, %s, %s, %s)",
                (data['username'], data['email'], data['password'], data['phone'])
            )
            conn.commit()
        return jsonify({"success": "Thank you for joining"}), 201
    except pymysql.err.IntegrityError:
        return jsonify({"error": "Email already registered"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/signin', methods=['POST'])
def signin():
    """User signin (for Soko Garden)"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    email = data.get('email', '')
    password = data.get('password', '')

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, email, phone FROM users WHERE email = %s AND password = %s",
                (email, password)
            )
            user = cur.fetchone()

        if not user:
            return jsonify({"message": "Login failed"}), 401

        return jsonify({"message": "Login success", "user": user}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# RUN APP
# ================================================================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)