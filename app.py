from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pymysql
import pymysql.cursors
import os
import jwt
import bcrypt
from datetime import datetime, timedelta, date, time
from functools import wraps
from dotenv import load_dotenv
import uuid
from werkzeug.utils import secure_filename

load_dotenv()

# ================================================================
# APP SETUP
# ================================================================
app = Flask(__name__)
# ================================================================
# UPLOAD CONFIGURATION
# ================================================================
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# Create folders
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'tournaments'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'teams'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'gallery'), exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_file(file, folder='tournaments'):
    """Save uploaded file and return filename"""
    if not file or file.filename == '':
        return None
    
    if not allowed_file(file.filename):
        raise ValueError(f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    
    # Create folder path
    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], folder)
    os.makedirs(upload_path, exist_ok=True)
    
    # Generate unique filename
    original_filename = secure_filename(file.filename)
    ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else 'jpg'
    unique_filename = f"{uuid.uuid4().hex}.{ext}"
    
    filepath = os.path.join(upload_path, unique_filename)
    file.save(filepath)
    
    return unique_filename

# ================================================================
# SERVE UPLOADED FILES
# ================================================================

@app.route('/uploads/<folder>/<filename>')
def uploaded_file(folder, filename):
    """Serve uploaded files"""
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], folder), filename)


# CORS - Allow your frontend domains
CORS(app, origins='*')

app.config['UPLOAD_FOLDER'] = 'static/uploads'

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


def ensure_gallery_table():
    """Create the gallery table if it does not exist."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tournament_gallery (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    tournament_name VARCHAR(255) NOT NULL,
                    image_title VARCHAR(255) NOT NULL,
                    image_url TEXT NOT NULL,
                    category VARCHAR(100) DEFAULT 'General',
                    display_order INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
    finally:
        conn.close()

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
            request.jwt_claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated


def can_manage_tournament(cur, tournament_id):
    """Allow the global admin or the user who owns the tournament."""
    claims = getattr(request, 'jwt_claims', {})
    if claims.get('role') == 'admin':
        return True

    cur.execute(
        "SELECT 1 FROM tournaments WHERE id = %s AND created_by = %s",
        (tournament_id, claims.get('identity'))
    )
    return cur.fetchone() is not None


def get_match_for_update(cur, match_id):
    """Lock a match and return its progression fields for a safe mutation."""
    cur.execute(
        """
         SELECT id, tournament_id, category_id, team_1_id, team_2_id,
             status, winner_id, next_match_id, next_team_slot, round
        FROM matches
        WHERE id = %s
        FOR UPDATE
        """,
        (match_id,)
    )
    return cur.fetchone()


def progression_would_cycle(cur, source_match_id, target_match_id):
    """Return True when following next_match_id from target reaches source."""
    current_id = target_match_id
    visited = set()
    while current_id and current_id not in visited:
        if current_id == source_match_id:
            return True
        visited.add(current_id)
        cur.execute("SELECT next_match_id FROM matches WHERE id = %s", (current_id,))
        row = cur.fetchone()
        current_id = row['next_match_id'] if row else None
    return False


def recalculate_category(cur, category_id):
    """Refresh group standings after a completed group-stage match."""
    cur.callproc('recalculate_standings', (category_id,))
    while cur.nextset():
        pass

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
        "version": "2.0.0",
        "endpoints": {
            "auth": {
                "signup": "/api/auth/signup",
                "signin": "/api/auth/signin",
                "admin_login": "/api/admin/login"
            },
            "tournaments": {
                "list": "/api/tournaments",
                "create": "/api/tournaments",
                "get": "/api/tournaments/<id>",
                "update": "/api/tournaments/<id>",
                "delete": "/api/tournaments/<id>"
            },
            "categories": {
                "list": "/api/categories/<tournament_id>",
                "create": "/api/categories",
                "get": "/api/categories/<id>",
                "update": "/api/categories/<id>",
                "delete": "/api/categories/<id>"
            },
            "teams": {
                "list": "/api/teams/<category_id>",
                "create": "/api/teams",
                "update": "/api/teams/<id>",
                "delete": "/api/teams/<id>"
            },
            "matches": {
                "list": "/api/matches",
                "create": "/api/matches",
                "get": "/api/matches/<id>",
                "update": "/api/matches/<id>",
                "complete": "/api/matches/<id>/complete",
                "delete": "/api/matches/<id>"
            },
            "standings": {
                "get": "/api/standings/<category_id>",
                "recalculate": "/api/standings/recalculate/<category_id>"
            },
            "gallery": {
                "list": "/api/gallery",
                "add": "/api/gallery",
                "delete": "/api/gallery/<id>"
            }
        }
    }), 200

# ================================================================
# AUTHENTICATION ENDPOINTS
# ================================================================

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    """User signup"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ['name', 'email', 'password', 'phone']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400

    # Hash the password
    hashed_password = bcrypt.hashpw(
        data['password'].encode('utf-8'), 
        bcrypt.gensalt()
    ).decode('utf-8')

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (name, email, password_hash, phone) 
                   VALUES (%s, %s, %s, %s)""",
                (data['name'], data['email'], hashed_password, data['phone'])
            )
            conn.commit()
            user_id = cur.lastrowid
            
            # Generate token
            token = jwt.encode(
                {
                    "identity": user_id,
                    "email": data['email'],
                    "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
                },
                JWT_SECRET,
                algorithm="HS256"
            )
            
        return jsonify({
            "success": True,
            "message": "User registered successfully",
            "data": {
                "id": user_id,
                "name": data['name'],
                "email": data['email'],
                "phone": data['phone'],
                "access_token": token
            }
        }), 201
    except pymysql.err.IntegrityError:
        return jsonify({"error": "Email already registered"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/auth/signin', methods=['POST'])
def signin():
    """User signin"""
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
                "SELECT id, name, email, password_hash, phone FROM users WHERE email = %s",
                (email,)
            )
            user = cur.fetchone()

        if not user:
            return jsonify({"error": "Invalid credentials"}), 401

        # Verify password
        if not bcrypt.checkpw(
            password.encode('utf-8'), 
            user['password_hash'].encode('utf-8')
        ):
            return jsonify({"error": "Invalid credentials"}), 401

        # Generate token
        token = jwt.encode(
            {
                "identity": user['id'],
                "email": user['email'],
                "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
            },
            JWT_SECRET,
            algorithm="HS256"
        )

        return jsonify({
            "success": True,
            "message": "Login successful",
            "data": {
                "id": user['id'],
                "name": user['name'],
                "email": user['email'],
                "phone": user.get('phone', ''),
                "access_token": token
            }
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

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
            "role": "admin",
            "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
        },
        JWT_SECRET,
        algorithm="HS256"
    )
    return jsonify({
        "success": True,
        "access_token": token,
        "message": "Login successful"
    }), 200

# ================================================================
# TOURNAMENT ENDPOINTS
# ================================================================
@app.route('/api/tournaments', methods=['GET'])
@token_required
def get_tournaments():
    """Get all tournaments for the current user"""
    token = request.headers.get('Authorization', '').split(' ')[1]
    decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    user_id = decoded.get('identity')
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, slogan, logo_url, season, status, share_token,
                       DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                       DATE_FORMAT(updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM tournaments 
                WHERE created_by = %s
                ORDER BY created_at DESC
            """, (user_id,))
            rows = cur.fetchall()
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        print(f"Error in get_tournaments: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()




@app.route('/api/tournaments/<int:tournament_id>/stats', methods=['GET'])
@token_required
def get_tournament_stats(tournament_id):
    """Get statistics for a specific tournament"""
    conn = None
    try:
        token = request.headers.get('Authorization', '').split(' ')[1]
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = decoded.get('identity')
        
        conn = get_db()
        with conn.cursor() as cur:
            # Verify tournament exists and belongs to user
            cur.execute("""
                SELECT id FROM tournaments 
                WHERE id = %s AND created_by = %s
            """, (tournament_id, user_id))
            tournament = cur.fetchone()
            
            if not tournament:
                return jsonify({"error": "Tournament not found or unauthorized"}), 404
            
            # Get category count
            cur.execute("""
                SELECT COUNT(*) as count FROM categories 
                WHERE tournament_id = %s
            """, (tournament_id,))
            categories = cur.fetchone()
            
            # Get team count
            cur.execute("""
                SELECT COUNT(*) as count FROM teams 
                WHERE tournament_id = %s
            """, (tournament_id,))
            teams = cur.fetchone()
            
            # Get match stats
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END) as scheduled,
                    SUM(CASE WHEN status = 'ongoing' THEN 1 ELSE 0 END) as ongoing,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM matches 
                WHERE tournament_id = %s
            """, (tournament_id,))
            matches = cur.fetchone()
            
        return jsonify({
            "success": True,
            "data": {
                "total_categories": categories['count'] or 0,
                "total_teams": teams['count'] or 0,
                "total_matches": matches['total'] or 0,
                "scheduled_matches": matches['scheduled'] or 0,
                "ongoing_matches": matches['ongoing'] or 0,
                "completed_matches": matches['completed'] or 0,
                "pending_registrations": 0
            }
        }), 200
        
    except Exception as e:
        print(f"Error in get_tournament_stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/tournaments/<int:tournament_id>', methods=['GET'])
def get_tournament(tournament_id):
    conn = None
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, slogan, logo_url, season, status, share_token,
                       DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                       DATE_FORMAT(updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM tournaments 
                WHERE id = %s
            """, (tournament_id,))
            tournament = cur.fetchone()
            
            if not tournament:
                return jsonify({"error": "Tournament not found"}), 404
            
            if tournament.get('logo_url') is None:
                tournament['logo_url'] = ''
                
        return jsonify({"success": True, "data": tournament}), 200
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/tournaments', methods=['POST'])
@token_required
def create_tournament():
    """Create a new tournament with optional logo"""
    conn = None
    try:
        # Check if file was uploaded
        logo_filename = None
        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename != '':
                logo_filename = save_uploaded_file(file, 'tournaments')
        
        # Get form data
        name = request.form.get('name')
        slogan = request.form.get('slogan', '')
        season = request.form.get('season')
        created_by = request.form.get('created_by')
        
        if not name or not season or not created_by:
            return jsonify({"error": "Missing required fields"}), 400
        
        import uuid
        share_token = str(uuid.uuid4())
        
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournaments 
                (name, slogan, season, share_token, created_by, logo_url)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (name, slogan, season, share_token, created_by, logo_filename))
            conn.commit()
            tournament_id = cur.lastrowid
            
            cur.execute("SELECT * FROM tournaments WHERE id = %s", (tournament_id,))
            tournament = safe_row(cur.fetchone())
            
        return jsonify({
            "success": True,
            "message": "Tournament created successfully",
            "data": tournament
        }), 201
        
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"❌ Error in create_tournament: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/api/tournaments/<int:tournament_id>', methods=['PUT'])
@token_required
def update_tournament(tournament_id):
    """Update a tournament"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    allowed_fields = ['name', 'slogan', 'logo_url', 'season', 'status']
    updates = []
    params = []
    
    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = %s")
            params.append(data[field])
    
    if not updates:
        return jsonify({"error": "No fields to update"}), 400
    
    params.append(tournament_id)
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM tournaments WHERE id = %s", (tournament_id,))
            if not cur.fetchone():
                return jsonify({"error": "Tournament not found"}), 404
            
            cur.execute(
                f"UPDATE tournaments SET {', '.join(updates)}, updated_at = NOW() WHERE id = %s",
                params
            )
            conn.commit()
            
            cur.execute("SELECT * FROM tournaments WHERE id = %s", (tournament_id,))
            tournament = safe_row(cur.fetchone())
            
        return jsonify({
            "success": True,
            "message": "Tournament updated successfully",
            "data": tournament
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/tournaments/<int:tournament_id>', methods=['DELETE'])
@token_required
def delete_tournament(tournament_id):
    """Delete a tournament"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM tournaments WHERE id = %s", (tournament_id,))
            tournament = cur.fetchone()
            
            if not tournament:
                return jsonify({"error": "Tournament not found"}), 404
            
            cur.execute("DELETE FROM tournaments WHERE id = %s", (tournament_id,))
            conn.commit()
            
        return jsonify({
            "success": True,
            "message": f"Tournament '{tournament['name']}' deleted successfully"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# CATEGORY ENDPOINTS - ✅ FIXED
# ================================================================

@app.route('/api/categories/<int:tournament_id>', methods=['GET'])
def get_categories(tournament_id):
    """Get all categories for a tournament"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, tournament_id, name, teams_per_group, status,
                       DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                       DATE_FORMAT(updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM categories 
                WHERE tournament_id = %s
                ORDER BY name
            """, (tournament_id,))
            rows = cur.fetchall()
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        print(f"❌ Error in get_categories: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/categories/<int:category_id>', methods=['GET'])
def get_category(category_id):
    """Get a single category"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, tournament_id, name, teams_per_group, status,
                       DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                       DATE_FORMAT(updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM categories 
                WHERE id = %s
            """, (category_id,))
            category = cur.fetchone()
            
            if not category:
                return jsonify({"error": "Category not found"}), 404
                
        return jsonify({"success": True, "data": category}), 200
    except Exception as e:
        print(f"❌ Error in get_category: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/categories', methods=['POST'])
@token_required
def create_category():
    """Create a new category"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    required = ['tournament_id', 'name']
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO categories 
                (tournament_id, name, teams_per_group, status) 
                VALUES (%s, %s, %s, 'setup')
            """, (
                data['tournament_id'],
                data['name'],
                data.get('teams_per_group', 4)
            ))
            conn.commit()
            category_id = cur.lastrowid
            
            cur.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
            category = safe_row(cur.fetchone())
            
        return jsonify({
            "success": True,
            "message": "Category created successfully",
            "data": category
        }), 201
    except pymysql.err.IntegrityError:
        return jsonify({"error": "Category name already exists for this tournament"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/categories/<int:category_id>', methods=['PUT'])
@token_required
def update_category(category_id):
    """Update a category"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    allowed_fields = ['name', 'teams_per_group', 'status']
    updates = []
    params = []
    
    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = %s")
            params.append(data[field])
    
    if not updates:
        return jsonify({"error": "No fields to update"}), 400
    
    params.append(category_id)
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM categories WHERE id = %s", (category_id,))
            if not cur.fetchone():
                return jsonify({"error": "Category not found"}), 404
            
            cur.execute(
                f"UPDATE categories SET {', '.join(updates)}, updated_at = NOW() WHERE id = %s",
                params
            )
            conn.commit()
            
            cur.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
            category = safe_row(cur.fetchone())
            
        return jsonify({
            "success": True,
            "message": "Category updated successfully",
            "data": category
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/categories/<int:category_id>', methods=['DELETE'])
@token_required
def delete_category(category_id):
    """Delete a category"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM categories WHERE id = %s", (category_id,))
            category = cur.fetchone()
            
            if not category:
                return jsonify({"error": "Category not found"}), 404
            
            cur.execute("DELETE FROM categories WHERE id = %s", (category_id,))
            conn.commit()
            
        return jsonify({
            "success": True,
            "message": f"Category '{category['name']}' deleted successfully"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# TEAM ENDPOINTS - ✅ FIXED
# ================================================================

@app.route('/api/teams/<int:category_id>', methods=['GET'])
def get_teams(category_id):
    """Get all teams in a category"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, tournament_id, category_id, name, logo_url, pool,
                       DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
                FROM teams 
                WHERE category_id = %s
                ORDER BY name
            """, (category_id,))
            rows = cur.fetchall()
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        print(f"❌ Error in get_teams: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/teams', methods=['POST'])
@token_required
def create_team():
    """Create a new team with optional logo"""
    conn = None
    try:
        # Check if file was uploaded
        logo_filename = None
        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename != '':
                logo_filename = save_uploaded_file(file, 'teams')
        
        # Get form data
        tournament_id = request.form.get('tournament_id')
        category_id = request.form.get('category_id')
        name = request.form.get('name')
        pool = request.form.get('pool')
        
        if not tournament_id or not category_id or not name:
            return jsonify({"error": "Missing required fields"}), 400
        
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO teams 
                (tournament_id, category_id, name, pool, logo_url)
                VALUES (%s, %s, %s, %s, %s)
            """, (tournament_id, category_id, name, pool, logo_filename))
            conn.commit()
            team_id = cur.lastrowid
            
            cur.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
            team = safe_row(cur.fetchone())
            
        return jsonify({
            "success": True,
            "message": "Team created successfully",
            "data": team
        }), 201
        
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        if conn:
            conn.close()
@app.route('/api/teams/<int:team_id>', methods=['PUT'])
@token_required
def update_team(team_id):
    """Update a team"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    allowed_fields = ['name', 'logo_url', 'pool']
    updates = []
    params = []
    
    for field in allowed_fields:
        if field in data:
            updates.append(f"{field} = %s")
            params.append(data[field])
    
    if not updates:
        return jsonify({"error": "No fields to update"}), 400
    
    params.append(team_id)
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM teams WHERE id = %s", (team_id,))
            if not cur.fetchone():
                return jsonify({"error": "Team not found"}), 404
            
            cur.execute(
                f"UPDATE teams SET {', '.join(updates)} WHERE id = %s",
                params
            )
            conn.commit()
            
            cur.execute("SELECT * FROM teams WHERE id = %s", (team_id,))
            team = safe_row(cur.fetchone())
            
        return jsonify({
            "success": True,
            "message": "Team updated successfully",
            "data": team
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/teams/<int:team_id>', methods=['DELETE'])
@token_required
def delete_team(team_id):
    """Delete a team"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM teams WHERE id = %s", (team_id,))
            team = cur.fetchone()
            
            if not team:
                return jsonify({"error": "Team not found"}), 404
            
            cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
            conn.commit()
            
        return jsonify({
            "success": True,
            "message": f"Team '{team['name']}' deleted successfully"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# STANDINGS ENDPOINTS - ✅ FIXED
# ================================================================

@app.route('/api/standings/<int:category_id>', methods=['GET'])
def get_standings(category_id):
    """Get standings for a category"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    s.rank,
                    t.id as team_id,
                    t.name as team_name,
                    t.logo_url,
                    s.pool,
                    s.played,
                    s.won,
                    s.drawn,
                    s.lost,
                    s.goals_for,
                    s.goals_against,
                    s.goal_diff,
                    s.points,
                    DATE_FORMAT(s.updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as last_updated
                FROM standings s
                JOIN teams t ON s.team_id = t.id
                WHERE s.category_id = %s
                ORDER BY s.rank
            """, (category_id,))
            standings = cur.fetchall()
            
        return jsonify({"success": True, "data": standings}), 200
    except Exception as e:
        print(f"❌ Error in get_standings: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/standings/recalculate/<int:category_id>', methods=['POST'])
@token_required
def recalculate_standings(category_id):
    """Recalculate standings for a category"""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.callproc('recalculate_standings', (category_id,))
            conn.commit()
            
        return jsonify({
            "success": True,
            "message": "Standings recalculated successfully"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ================================================================
# KNOCKOUT BRACKET ENDPOINTS
# ================================================================
def bracket_round_name(team_count):
    names = {2: 'Final', 4: 'Semi-final', 8: 'Quarter-final'}
    return names.get(team_count, f'Round of {team_count}')


@app.route('/api/categories/<int:category_id>/bracket', methods=['GET'])
def get_bracket(category_id):
    """Public bracket data grouped by generated knockout round."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tournament_id, name FROM categories WHERE id = %s", (category_id,))
            category = cur.fetchone()
            if not category:
                return jsonify({"error": "Category not found"}), 404

            cur.execute(
                """
                SELECT m.id, m.round, m.status, m.team_1_id, m.team_2_id,
                       m.team_1_score, m.team_2_score, m.winner_id,
                       m.next_match_id, m.next_team_slot,
                       t1.name AS team_1_name, t2.name AS team_2_name,
                       w.name AS winner_name, m.match_time, m.venue
                FROM matches m
                LEFT JOIN teams t1 ON t1.id = m.team_1_id
                LEFT JOIN teams t2 ON t2.id = m.team_2_id
                LEFT JOIN teams w ON w.id = m.winner_id
                WHERE m.category_id = %s AND m.bracket_batch_id = (
                    SELECT MAX(bracket_batch_id) FROM matches WHERE category_id = %s
                )
                ORDER BY m.match_time, m.id
                """,
                (category_id, category_id)
            )
            matches = [safe_row(row) for row in cur.fetchall()]

        rounds = {}
        for match in matches:
            rounds.setdefault(match['round'], []).append(match)
        return jsonify({
            "success": True,
            "data": {
                "category_id": category_id,
                "tournament_id": category['tournament_id'],
                "category_name": category['name'],
                "rounds": rounds,
                "matches": matches,
            }
        }), 200
    except Exception as e:
        print(f"❌ Error in get_bracket: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/categories/<int:category_id>/bracket/generate', methods=['POST'])
@token_required
def generate_bracket(category_id):
    """Generate a single-elimination bracket from current group standings."""
    data = request.get_json(silent=True) or {}
    qualifiers_per_pool = int(data.get('qualifiers_per_pool', 2))
    if qualifiers_per_pool < 1 or qualifiers_per_pool > 8:
        return jsonify({"error": "qualifiers_per_pool must be between 1 and 8"}), 400

    conn = get_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("SELECT tournament_id, name FROM categories WHERE id = %s FOR UPDATE", (category_id,))
            category = cur.fetchone()
            if not category:
                conn.rollback()
                return jsonify({"error": "Category not found"}), 404
            if not can_manage_tournament(cur, category['tournament_id']):
                conn.rollback()
                return jsonify({"error": "You do not have admin access to this tournament"}), 403

            cur.execute(
                "SELECT COUNT(*) AS count FROM matches WHERE category_id = %s AND bracket_batch_id IS NOT NULL",
                (category_id,)
            )
            if cur.fetchone()['count']:
                conn.rollback()
                return jsonify({"error": "A knockout bracket already exists. Reset it before generating a new one."}), 409

            recalculate_category(cur, category_id)
            cur.execute(
                """
                SELECT s.team_id, s.pool, s.rank, t.name
                FROM standings s
                JOIN teams t ON t.id = s.team_id
                WHERE s.category_id = %s
                ORDER BY COALESCE(s.pool, ''), s.rank, s.team_id
                """,
                (category_id,)
            )
            standings = cur.fetchall()
            if not standings:
                conn.rollback()
                return jsonify({"error": "No standings are available to generate a bracket"}), 400

            pools = {}
            for row in standings:
                pools.setdefault(row['pool'] or '__all__', []).append(row)
            qualifiers = []
            for pool_name, pool_rows in pools.items():
                for row in pool_rows[:qualifiers_per_pool]:
                    qualifiers.append(row)

            if len(qualifiers) < 2:
                conn.rollback()
                return jsonify({"error": "At least two qualifying teams are required"}), 400

            bracket_size = 1
            while bracket_size < len(qualifiers):
                bracket_size *= 2
            while len(qualifiers) < bracket_size:
                qualifiers.append(None)

            batch_id = str(uuid.uuid4())
            round_matches = []
            current_size = bracket_size
            current_teams = qualifiers
            while current_size >= 2:
                round_name = bracket_round_name(current_size)
                match_ids = []
                for index in range(0, current_size, 2):
                    team_1 = current_teams[index]
                    team_2 = current_teams[index + 1]
                    cur.execute(
                        """
                        INSERT INTO matches
                        (tournament_id, category_id, team_1_id, team_2_id, round, match_time,
                         venue, status, bracket_batch_id)
                        VALUES (%s, %s, %s, %s, %s, NOW(), 'TBD', 'scheduled', %s)
                        """,
                        (
                            category['tournament_id'], category_id,
                            team_1['team_id'] if team_1 else None,
                            team_2['team_id'] if team_2 else None,
                            round_name, batch_id
                        )
                    )
                    match_ids.append(cur.lastrowid)
                round_matches.append(match_ids)
                current_teams = [None] * (current_size // 2)
                current_size //= 2

            for source_round, target_round in zip(round_matches, round_matches[1:]):
                for index, source_id in enumerate(source_round):
                    target_id = target_round[index // 2]
                    slot = 'team_1' if index % 2 == 0 else 'team_2'
                    cur.execute(
                        "UPDATE matches SET next_match_id = %s, next_team_slot = %s WHERE id = %s",
                        (target_id, slot, source_id)
                    )

            # Resolve byes immediately, including chains of byes.
            for match_ids in round_matches[:-1]:
                for match_id in match_ids:
                    cur.execute(
                        "SELECT team_1_id, team_2_id, next_match_id, next_team_slot FROM matches WHERE id = %s FOR UPDATE",
                        (match_id,)
                    )
                    match = cur.fetchone()
                    if bool(match['team_1_id']) == bool(match['team_2_id']):
                        continue
                    winner_id = match['team_1_id'] or match['team_2_id']
                    cur.execute(
                        "UPDATE matches SET winner_id = %s, status = 'completed' WHERE id = %s",
                        (winner_id, match_id)
                    )
                    cur.execute(
                        f"UPDATE matches SET {match['next_team_slot']}_id = %s WHERE id = %s",
                        (winner_id, match['next_match_id'])
                    )

            conn.commit()
            return jsonify({
                "success": True,
                "message": "Knockout bracket generated successfully",
                "data": {"batch_id": batch_id, "qualifiers": len(qualifiers), "category_id": category_id}
            }), 201
    except Exception as e:
        conn.rollback()
        print(f"❌ Error in generate_bracket: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/categories/<int:category_id>/bracket/reset', methods=['POST'])
@token_required
def reset_bracket(category_id):
    """Remove the generated knockout bracket without touching group matches."""
    conn = get_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("SELECT tournament_id FROM categories WHERE id = %s FOR UPDATE", (category_id,))
            category = cur.fetchone()
            if not category:
                conn.rollback()
                return jsonify({"error": "Category not found"}), 404
            if not can_manage_tournament(cur, category['tournament_id']):
                conn.rollback()
                return jsonify({"error": "You do not have admin access to this tournament"}), 403
            cur.execute(
                "SELECT COUNT(*) AS completed FROM matches WHERE category_id = %s AND bracket_batch_id IS NOT NULL AND status = 'completed'",
                (category_id,)
            )
            if cur.fetchone()['completed']:
                conn.rollback()
                return jsonify({"error": "Cannot reset a bracket after a knockout match has been completed"}), 409
            cur.execute("DELETE FROM matches WHERE category_id = %s AND bracket_batch_id IS NOT NULL", (category_id,))
            deleted = cur.rowcount
            conn.commit()
        return jsonify({"success": True, "message": "Knockout bracket reset", "data": {"deleted": deleted}}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# MATCH ENDPOINTS
# ================================================================

@app.route('/api/matches', methods=['GET'])
def get_matches():
    """Get all matches with optional filters"""
    category_id = request.args.get('category_id')
    pool = request.args.get('pool')
    status = request.args.get('status')
    tournament_id = request.args.get('tournament_id')
    
    sql = """
        SELECT 
            m.*,
            t1.name as team_1_name,
            t1.logo_url as team_1_logo,
            t2.name as team_2_name,
            t2.logo_url as team_2_logo,
            w.name as winner_name,
            DATE_FORMAT(m.match_time, '%%Y-%%m-%%d %%H:%%i') as match_time_formatted,
            DATE_FORMAT(m.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
            DATE_FORMAT(m.updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
        FROM matches m
        LEFT JOIN teams t1 ON m.team_1_id = t1.id
        LEFT JOIN teams t2 ON m.team_2_id = t2.id
        LEFT JOIN teams w ON m.winner_id = w.id
        WHERE 1=1
    """
    params = []
    
    if category_id:
        sql += " AND m.category_id = %s"
        params.append(category_id)
    if pool:
        sql += " AND m.pool = %s"
        params.append(pool)
    if status:
        sql += " AND m.status = %s"
        params.append(status)
    if tournament_id:
        sql += " AND m.tournament_id = %s"
        params.append(tournament_id)
    
    sql += " ORDER BY m.match_time, m.id"
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        print(f"❌ Error in get_matches: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/matches/today', methods=['GET'])
def get_today_matches():
    """Get matches scheduled for today"""
    today = datetime.utcnow().strftime('%Y-%m-%d')
    sql = """
        SELECT 
            m.*,
            t1.name as team_1_name,
            t1.logo_url as team_1_logo,
            t2.name as team_2_name,
            t2.logo_url as team_2_logo,
            w.name as winner_name,
            DATE_FORMAT(m.match_time, '%%Y-%%m-%%d %%H:%%i') as match_time_formatted
        FROM matches m
        LEFT JOIN teams t1 ON m.team_1_id = t1.id
        LEFT JOIN teams t2 ON m.team_2_id = t2.id
        LEFT JOIN teams w ON m.winner_id = w.id
        WHERE DATE(m.match_time) = %s
        ORDER BY m.match_time
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (today,))
            rows = cur.fetchall()
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        print(f"❌ Error in get_today_matches: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/matches/<int:match_id>', methods=['GET'])
def get_match(match_id):
    """Get one match"""
    sql = """
        SELECT 
            m.*,
            t1.name as team_1_name,
            t1.logo_url as team_1_logo,
            t2.name as team_2_name,
            t2.logo_url as team_2_logo,
            w.name as winner_name,
            DATE_FORMAT(m.match_time, '%%Y-%%m-%%d %%H:%%i') as match_time_formatted,
            DATE_FORMAT(m.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
            DATE_FORMAT(m.updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
        FROM matches m
        LEFT JOIN teams t1 ON m.team_1_id = t1.id
        LEFT JOIN teams t2 ON m.team_2_id = t2.id
        LEFT JOIN teams w ON m.winner_id = w.id
        WHERE m.id = %s
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (match_id,))
            match = cur.fetchone()
        if not match:
            return jsonify({"error": "Match not found"}), 404
        return jsonify({"success": True, "data": match}), 200
    except Exception as e:
        print(f"❌ Error in get_match: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/matches', methods=['POST'])
@token_required
def create_match():
    """Create a new match"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ['tournament_id', 'category_id', 'team_1_id', 'team_2_id', 'match_time', 'venue']
    for field in required:
        if field not in data or data.get(field) in [None, '']:
            return jsonify({"error": f"Missing field: {field}"}), 400

    if int(data['team_1_id']) == int(data['team_2_id']):
        return jsonify({"error": "Teams must be different"}), 400

    next_match_id = data.get('next_match_id') or None
    next_team_slot = data.get('next_team_slot') or None
    if next_team_slot not in [None, 'team_1', 'team_2']:
        return jsonify({"error": "next_team_slot must be team_1 or team_2"}), 400
    if next_match_id is not None:
        try:
            next_match_id = int(next_match_id)
        except (TypeError, ValueError):
            return jsonify({"error": "next_match_id must be a valid match ID"}), 400

    match_time = str(data['match_time']).replace('T', ' ')
    try:
        datetime.strptime(match_time, '%Y-%m-%d %H:%M')
    except ValueError:
        try:
            datetime.strptime(match_time, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return jsonify({"error": "Invalid match_time format"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            if not can_manage_tournament(cur, int(data['tournament_id'])):
                return jsonify({"error": "You do not have admin access to this tournament"}), 403

            cur.execute(
                """
                SELECT c.id
                FROM categories c
                JOIN teams t1 ON t1.id = %s AND t1.category_id = c.id
                JOIN teams t2 ON t2.id = %s AND t2.category_id = c.id
                WHERE c.id = %s AND c.tournament_id = %s
                """,
                (
                    int(data['team_1_id']),
                    int(data['team_2_id']),
                    int(data['category_id']),
                    int(data['tournament_id']),
                )
            )
            if not cur.fetchone():
                return jsonify({"error": "Both teams must belong to the selected category and tournament"}), 400

            if next_match_id is not None:
                if next_team_slot is None:
                    return jsonify({"error": "next_team_slot is required when next_match_id is set"}), 400
                cur.execute(
                    """
                    SELECT id, tournament_id, category_id, team_1_id, team_2_id
                    FROM matches
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (next_match_id,)
                )
                target = cur.fetchone()
                if not target:
                    return jsonify({"error": "The configured next match does not exist"}), 400
                if target['tournament_id'] != int(data['tournament_id']) or target['category_id'] != int(data['category_id']):
                    return jsonify({"error": "The next match must use the same tournament and category"}), 400
                if target[f'{next_team_slot}_id']:
                    return jsonify({"error": "The selected slot in the next match is already occupied"}), 400
            elif next_team_slot is not None:
                return jsonify({"error": "next_match_id is required when next_team_slot is set"}), 400

            cur.execute(
                """
                INSERT INTO matches
                (tournament_id, category_id, team_1_id, team_2_id, pool, round, match_time, venue, status,
                 next_match_id, next_team_slot)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'scheduled', %s, %s)
                """,
                (
                    int(data['tournament_id']),
                    int(data['category_id']),
                    int(data['team_1_id']),
                    int(data['team_2_id']),
                    data.get('pool') or None,
                    data.get('round') or 'Group Stage',
                    match_time,
                    data['venue'],
                    next_match_id,
                    next_team_slot,
                )
            )
            conn.commit()
            match_id = cur.lastrowid

            cur.execute("""
                SELECT 
                    m.*,
                    t1.name as team_1_name,
                    t1.logo_url as team_1_logo,
                    t2.name as team_2_name,
                    t2.logo_url as team_2_logo,
                    w.name as winner_name,
                    DATE_FORMAT(m.match_time, '%%Y-%%m-%%d %%H:%%i') as match_time_formatted,
                    DATE_FORMAT(m.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                    DATE_FORMAT(m.updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM matches m
                LEFT JOIN teams t1 ON m.team_1_id = t1.id
                LEFT JOIN teams t2 ON m.team_2_id = t2.id
                LEFT JOIN teams w ON m.winner_id = w.id
                WHERE m.id = %s
            """, (match_id,))
            match = safe_row(cur.fetchone())

        return jsonify({
            "success": True,
            "message": "Match created successfully",
            "data": match
        }), 201
    except pymysql.err.IntegrityError as e:
        print(f"❌ Error in create_match: {e}")
        return jsonify({"error": "Could not create match. Check selected teams and tournament."}), 400
    except Exception as e:
        print(f"❌ Error in create_match: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/matches/<int:match_id>', methods=['PUT'])
@token_required
def update_match(match_id):
    """Update an existing match"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    allowed_fields = [
        'category_id', 'team_1_id', 'team_2_id', 'pool', 'round',
        'match_time', 'venue', 'next_match_id', 'next_team_slot'
    ]
    updates = []
    params = []

    if 'team_1_id' in data and 'team_2_id' in data:
        if int(data['team_1_id']) == int(data['team_2_id']):
            return jsonify({"error": "Teams must be different"}), 400

    if 'next_team_slot' in data and data['next_team_slot'] not in [None, '', 'team_1', 'team_2']:
        return jsonify({"error": "next_team_slot must be team_1 or team_2"}), 400

    if data.get('next_match_id') not in [None, '']:
        try:
            data['next_match_id'] = int(data['next_match_id'])
        except (TypeError, ValueError):
            return jsonify({"error": "next_match_id must be a valid match ID"}), 400

    for field in allowed_fields:
        if field not in data:
            continue

        value = data[field]
        if field == 'match_time' and value not in [None, '']:
            match_time = str(value).replace('T', ' ')
            try:
                datetime.strptime(match_time, '%Y-%m-%d %H:%M')
            except ValueError:
                try:
                    datetime.strptime(match_time, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return jsonify({"error": "Invalid match_time format"}), 400
            updates.append(f"{field} = %s")
            params.append(match_time)
        elif field in ['category_id', 'team_1_id', 'team_2_id', 'next_match_id'] and value not in [None, '']:
            updates.append(f"{field} = %s")
            params.append(int(value))
        else:
            if value in [None, '']:
                value = None
            updates.append(f"{field} = %s")
            params.append(value)

    if not updates:
        return jsonify({"error": "No fields to update"}), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            current_match = get_match_for_update(cur, match_id)
            if not current_match:
                return jsonify({"error": "Match not found"}), 404
            if not can_manage_tournament(cur, current_match['tournament_id']):
                return jsonify({"error": "You do not have admin access to this tournament"}), 403
            if current_match['status'] == 'completed':
                return jsonify({"error": "Completed matches cannot be edited"}), 409

            next_match_id = data.get('next_match_id', current_match['next_match_id'])
            next_team_slot = data.get('next_team_slot', current_match['next_team_slot'])
            if next_match_id in ['', None]:
                next_match_id = None
            if next_team_slot in ['', None]:
                next_team_slot = None
            if next_match_id is not None and next_team_slot not in ['team_1', 'team_2']:
                return jsonify({"error": "A next match requires a valid winner slot"}), 400
            if next_match_id is None and next_team_slot is not None:
                return jsonify({"error": "next_team_slot requires next_match_id"}), 400
            if next_match_id == match_id:
                return jsonify({"error": "A match cannot advance to itself"}), 400
            if next_match_id is not None:
                cur.execute(
                    """
                    SELECT id, tournament_id, category_id, team_1_id, team_2_id
                    FROM matches
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (next_match_id,)
                )
                target = cur.fetchone()
                if not target:
                    return jsonify({"error": "The configured next match does not exist"}), 400
                if target['tournament_id'] != current_match['tournament_id'] or target['category_id'] != current_match['category_id']:
                    return jsonify({"error": "The next match must use the same tournament and category"}), 400
                if progression_would_cycle(cur, match_id, next_match_id):
                    return jsonify({"error": "The progression links would create a cycle"}), 400
                if target[f'{next_team_slot}_id'] and next_match_id != current_match['next_match_id']:
                    return jsonify({"error": "The selected slot in the next match is already occupied"}), 400

            query = f"UPDATE matches SET {', '.join(updates)} WHERE id = %s"
            params.append(match_id)
            cur.execute(query, params)
            conn.commit()

            cur.execute("""
                SELECT 
                    m.*,
                    t1.name as team_1_name,
                    t1.logo_url as team_1_logo,
                    t2.name as team_2_name,
                    t2.logo_url as team_2_logo,
                    w.name as winner_name,
                    DATE_FORMAT(m.match_time, '%%Y-%%m-%%d %%H:%%i') as match_time_formatted,
                    DATE_FORMAT(m.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                    DATE_FORMAT(m.updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM matches m
                LEFT JOIN teams t1 ON m.team_1_id = t1.id
                LEFT JOIN teams t2 ON m.team_2_id = t2.id
                LEFT JOIN teams w ON m.winner_id = w.id
                WHERE m.id = %s
            """, (match_id,))
            match = safe_row(cur.fetchone())

        return jsonify({
            "success": True,
            "message": "Match updated successfully",
            "data": match
        }), 200
    except Exception as e:
        print(f"❌ Error in update_match: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/matches/<int:match_id>/complete', methods=['POST'])
@token_required
def complete_match(match_id):
    """Mark a match as complete and save the score"""
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400

    if 'team_1_score' not in data or 'team_2_score' not in data:
        return jsonify({"error": "team_1_score and team_2_score are required"}), 400

    try:
        team_1_score = int(data['team_1_score'])
        team_2_score = int(data['team_2_score'])
    except (TypeError, ValueError):
        return jsonify({"error": "Scores must be whole numbers"}), 400
    if team_1_score < 0 or team_2_score < 0:
        return jsonify({"error": "Scores cannot be negative"}), 400

    conn = get_db()
    try:
        conn.begin()
        with conn.cursor() as cur:
            match = get_match_for_update(cur, match_id)
            if not match:
                return jsonify({"error": "Match not found"}), 404
            if not can_manage_tournament(cur, match['tournament_id']):
                return jsonify({"error": "You do not have admin access to this tournament"}), 403
            if match['status'] == 'completed':
                return jsonify({"error": "This match has already been completed"}), 409
            if not match['team_1_id'] or not match['team_2_id']:
                return jsonify({"error": "Both teams must be assigned before completing this match"}), 400

            if team_1_score > team_2_score:
                winner_id = match['team_1_id']
            elif team_2_score > team_1_score:
                winner_id = match['team_2_id']
            else:
                winner_id = None

            next_match_id = match['next_match_id']
            next_team_slot = match['next_team_slot']
            if winner_id and next_match_id:
                if next_team_slot not in ['team_1', 'team_2']:
                    conn.rollback()
                    return jsonify({"error": "This match has no valid winner slot configured"}), 400
                cur.execute(
                    """
                    SELECT id, tournament_id, category_id, team_1_id, team_2_id
                    FROM matches
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (next_match_id,)
                )
                target = cur.fetchone()
                if not target:
                    conn.rollback()
                    return jsonify({"error": "The configured next match does not exist"}), 400
                if target['tournament_id'] != match['tournament_id'] or target['category_id'] != match['category_id']:
                    conn.rollback()
                    return jsonify({"error": "The next match must use the same tournament and category"}), 400
                if target[f'{next_team_slot}_id'] not in [None, match['winner_id']]:
                    conn.rollback()
                    return jsonify({"error": "The next match slot is already occupied"}), 409
            elif next_match_id and not winner_id:
                conn.rollback()
                return jsonify({"error": "A tied match cannot advance without a winner"}), 400
            elif not winner_id and match['round'] != 'Group Stage':
                conn.rollback()
                return jsonify({"error": "Knockout matches require a winner to advance"}), 400

            cur.execute(
                """
                UPDATE matches
                SET team_1_score = %s,
                    team_2_score = %s,
                    winner_id = %s,
                    status = 'completed'
                WHERE id = %s
                """,
                (team_1_score, team_2_score, winner_id, match_id)
            )

            if winner_id and next_match_id:
                cur.execute(
                    f"UPDATE matches SET {next_team_slot}_id = %s WHERE id = %s",
                    (winner_id, next_match_id)
                )

            if match['round'] == 'Group Stage':
                recalculate_category(cur, match['category_id'])

            conn.commit()

            cur.execute("""
                SELECT 
                    m.*,
                    t1.name as team_1_name,
                    t1.logo_url as team_1_logo,
                    t2.name as team_2_name,
                    t2.logo_url as team_2_logo,
                    w.name as winner_name,
                    DATE_FORMAT(m.match_time, '%%Y-%%m-%%d %%H:%%i') as match_time_formatted,
                    DATE_FORMAT(m.created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at,
                    DATE_FORMAT(m.updated_at, '%%Y-%%m-%%d %%H:%%i:%%s') as updated_at
                FROM matches m
                LEFT JOIN teams t1 ON m.team_1_id = t1.id
                LEFT JOIN teams t2 ON m.team_2_id = t2.id
                LEFT JOIN teams w ON m.winner_id = w.id
                WHERE m.id = %s
            """, (match_id,))
            updated = safe_row(cur.fetchone())

        return jsonify({
            "success": True,
            "message": "Match completed successfully",
            "data": updated
        }), 200
    except Exception as e:
        conn.rollback()
        print(f"❌ Error in complete_match: {e}")
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
            match = get_match_for_update(cur, match_id)
            if not match:
                return jsonify({"error": "Match not found"}), 404
            if not can_manage_tournament(cur, match['tournament_id']):
                return jsonify({"error": "You do not have admin access to this tournament"}), 403
            if match['status'] == 'completed':
                return jsonify({"error": "Completed matches cannot be deleted"}), 409

            cur.execute("SELECT 1 FROM matches WHERE next_match_id = %s LIMIT 1", (match_id,))
            if cur.fetchone():
                return jsonify({"error": "This match is linked as a progression target and cannot be deleted"}), 409

            cur.execute("DELETE FROM matches WHERE id = %s", (match_id,))
            conn.commit()

        return jsonify({
            "success": True,
            "message": "Match deleted successfully"
        }), 200
    except Exception as e:
        print(f"❌ Error in delete_match: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# ================================================================
# GALLERY ENDPOINTS
# ================================================================

@app.route('/api/gallery', methods=['GET'])
def get_gallery():
    """Get gallery images - Public endpoint (no token required)"""
    ensure_gallery_table()
    tournament = request.args.get('tournament', 'Kaizen Cup 2026')
    category = request.args.get('category')
    
    sql = """
        SELECT id, tournament_name, image_title, image_url, category, display_order,
               DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
        FROM tournament_gallery 
        WHERE tournament_name = %s
    """
    params = [tournament]
    
    if category:
        sql += " AND category = %s"
        params.append(category)
    
    sql += " ORDER BY display_order, created_at DESC"
    
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return jsonify({"success": True, "data": rows}), 200
    except Exception as e:
        print(f"❌ Error in get_gallery: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route('/api/gallery', methods=['POST'])
@token_required
def create_gallery_image():
    """Create a gallery image from a local upload or a direct URL."""
    ensure_gallery_table()
    try:
        image_title = (request.form.get('image_title') or '').strip()
        category = request.form.get('category') or 'General'
        display_order = request.form.get('display_order') or 0
        tournament_name = request.form.get('tournament_name') or request.form.get('tournament') or 'Kaizen Cup 2026'
        image_url = (request.form.get('image_url') or '').strip()
        file = request.files.get('image')

        if not image_title:
            return jsonify({"error": "Image title is required"}), 400

        final_image_url = image_url
        if file and file.filename:
            final_image_url = f"/uploads/gallery/{save_uploaded_file(file, 'gallery')}"
        elif not final_image_url:
            return jsonify({"error": "Please upload an image or provide an image URL"}), 400

        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tournament_gallery (tournament_name, image_title, image_url, category, display_order)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (tournament_name, image_title, final_image_url, category, int(display_order))
                )
                conn.commit()
                cur.execute(
                    """
                    SELECT id, tournament_name, image_title, image_url, category, display_order,
                           DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
                    FROM tournament_gallery
                    WHERE id = LAST_INSERT_ID()
                    """
                )
                row = cur.fetchone()

            return jsonify({"success": True, "message": "Image added successfully", "data": row}), 201
        finally:
            conn.close()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"❌ Error in create_gallery_image: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/gallery/<int:image_id>', methods=['DELETE'])
@token_required
def delete_gallery_image(image_id):
    """Delete a gallery image record and its uploaded file when present."""
    ensure_gallery_table()
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT image_url FROM tournament_gallery WHERE id = %s", (image_id,))
            image = cur.fetchone()
            if not image:
                return jsonify({"error": "Image not found"}), 404

            image_url = image.get('image_url') or ''
            if image_url.startswith('/uploads/gallery/'):
                filename = image_url.split('/uploads/gallery/')[-1]
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'gallery', filename)
                if os.path.exists(file_path):
                    os.remove(file_path)

            cur.execute("DELETE FROM tournament_gallery WHERE id = %s", (image_id,))
            conn.commit()

        return jsonify({"success": True, "message": "Image deleted successfully"}), 200
    except Exception as e:
        print(f"❌ Error in delete_gallery_image: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ================================================================
# IMAGE UPLOAD ENDPOINT
# ================================================================

@app.route('/api/upload/logo', methods=['POST'])
@token_required
def upload_logo():
    """Upload logo for tournament or team"""
    try:
        upload_type = request.form.get('type')  # 'tournament' or 'team'
        item_id = request.form.get('item_id')
        
        if not upload_type or not item_id:
            return jsonify({"error": "Missing type or item_id"}), 400
        
        if 'logo' not in request.files:
            return jsonify({"error": "No logo file provided"}), 400
        
        file = request.files['logo']
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Save file
        filename = save_uploaded_file(file, upload_type + 's')  # tournaments or teams
        
        # Update database
        conn = get_db()
        try:
            with conn.cursor() as cur:
                # Get old logo filename to delete
                if upload_type == 'tournament':
                    cur.execute("SELECT logo_url FROM tournaments WHERE id = %s", (item_id,))
                    old = cur.fetchone()
                    if old and old.get('logo_url'):
                        old_path = os.path.join(app.config['UPLOAD_FOLDER'], 'tournaments', old['logo_url'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    
                    cur.execute(
                        "UPDATE tournaments SET logo_url = %s WHERE id = %s",
                        (filename, item_id)
                    )
                    
                elif upload_type == 'team':
                    cur.execute("SELECT logo_url FROM teams WHERE id = %s", (item_id,))
                    old = cur.fetchone()
                    if old and old.get('logo_url'):
                        old_path = os.path.join(app.config['UPLOAD_FOLDER'], 'teams', old['logo_url'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                    
                    cur.execute(
                        "UPDATE teams SET logo_url = %s WHERE id = %s",
                        (filename, item_id)
                    )
                else:
                    return jsonify({"error": "Invalid type. Use 'tournament' or 'team'"}), 400
                
                conn.commit()
        finally:
            conn.close()
        
        return jsonify({
            "success": True,
            "message": "Logo uploaded successfully",
            "data": {
                "filename": filename,
                "url": f"/uploads/{upload_type}s/{filename}"
            }
        }), 201
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"❌ Error in upload_logo: {e}")
        return jsonify({"error": str(e)}), 500
# ... (rest of gallery endpoints remain the same)

# ================================================================
# RUN APP
# ================================================================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)