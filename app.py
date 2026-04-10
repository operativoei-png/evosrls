import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template, request, send_file, url_for, send_from_directory
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from openpyxl import Workbook, load_workbook

# --- CONFIGURAZIONE ---
db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

ALLOWED_CERT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "evolve-industrial-2026")
    
    # FIX PER RENDER: Converte postgres:// in postgresql:// per SQLAlchemy
    db_url = os.getenv("DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "evolve.db"))
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Gestione Cartelle Upload
    app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
    app.config["CERT_FOLDER"] = os.path.join(app.config["UPLOAD_FOLDER"], "attestati")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["CERT_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        # Creazione Admin Default
        if not User.query.filter_by(username="admin").first():
            u = User(username="admin", role="admin")
            u.set_password("admin123!")
            db.session.add(u)
            db.session.commit()
        if not AppSetting.query.first():
            db.session.add(AppSetting())
            db.session.commit()

    @app.context_processor
    def inject_branding():
        return {"app_settings": AppSetting.query.first()}

    register_routes(app)
    return app

# --- MODELLI ---

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), default="admin")
    def set_password(self, raw): self.password_hash = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password_hash, raw)

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), default="Evolve Impianti")
    logo_path = db.Column(db.String(255), default="")

class Technician(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(50), default="")
    notes = db.Column(db.String(255), default="")
    certificates = db.relationship("Certificate", backref="technician", cascade="all, delete-orphan")
    tools = db.relationship("Tool", backref="technician")
    vans = db.relationship("Van", backref="technician")

class Certificate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    technician_id = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    description = db.Column(db.String(150))
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)

class WarehouseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    serial = db.Column(db.String(120), unique=True, nullable=False, index=True) # Cuore del sistema a barcode
    code = db.Column(db.String(80), nullable=False) # Codice articolo generico
    description = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="generale") # "generale", "in_viaggio", "installato"
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=True)
    last_update = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    technician = db.relationship("Technician", backref="mobile_items")

class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=True)

class Van(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(30), unique=True, nullable=False)
    model = db.Column(db.String(120))
    assigned_to = db.Column(db.Integer, db.ForeignKey("technician.id"), nullable=True)

# --- ROTTE E LOGICHE DI BUSINESS ---

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

def register_routes(app):
    @app.route("/")
    def home(): return redirect(url_for("dashboard" if current_user.is_authenticated else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = User.query.filter_by(username=request.form.get("username")).first()
            if user and user.check_password(request.form.get("password")):
                login_user(user)
                return redirect(url_for("dashboard"))
            flash("Credenziali non valide", "error")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout(): logout_user(); return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        stats = {
            "technicians": Technician.query.count(),
            "items_general": WarehouseItem.query.filter_by(status="generale").count(),
            "items_traveling": WarehouseItem.query.filter_by(status="in_viaggio").count(),
            "items_installed": WarehouseItem.query.filter_by(status="installato").count()
        }
        return render_template("dashboard.html", stats=stats)

    # --- LOGICA BARCODE E MAGAZZINO ---
    
    @app.route("/scan_barcode", methods=["POST"])
    @login_required
    def scan_barcode():
        serial = request.form.get("serial")
        tech_id = request.form.get("technician_id") # Opzionale
        code = request.form.get("code", "N/A")
        desc = request.form.get("description", "Articolo Sconosciuto")

        if not serial:
            flash("Errore: Seriale mancante.", "error")
            return redirect(request.referrer or url_for("warehouse"))

        item = WarehouseItem.query.filter_by(serial=serial).first()

        if not item:
            # 1. IL SERIALE NON ESISTE: Lo creiamo in Magazzino Generale
            new_item = WarehouseItem(serial=serial, code=code, description=desc, status="generale")
            db.session.add(new_item)
            flash(f"Caricato nuovo seriale {serial} in Magazzino Generale.", "success")
        
        else:
            # 2. IL SERIALE ESISTE: Gestiamo il trasferimento
            if item.status == "installato":
                flash(f"Attenzione! L'articolo {serial} risulta già installato in passato.", "error")
            
            elif tech_id:
                # Assegnazione al Tecnico (Da Generale a In Viaggio)
                item.status = "in_viaggio"
                item.assigned_to = tech_id
                flash(f"Seriale {serial} trasferito al furgone del tecnico.", "success")
            
            else:
                # Rientro in Sede (Da In Viaggio a Generale)
                item.status = "generale"
                item.assigned_to = None
                flash(f"Seriale {serial} rientrato in Magazzino Generale.", "info")

        db.session.commit()
        return redirect(request.referrer or url_for("warehouse"))

    @app.route("/install_item/<int:item_id>", methods=["POST"])
    @login_required
    def install_item(item_id):
        item = WarehouseItem.query.get_or_404(item_id)
        if item.status == "in_viaggio":
            item.status = "installato"
            db.session.commit()
            flash(f"Articolo {item.serial} segnato come INSTALLATO.", "success")
        return redirect(url_for("technician_detail", tech_id=item.assigned_to))

    @app.route("/import_excel", methods=["POST"])
    @login_required
    def import_excel():
        file = request.files.get("excel_file")
        if not file: return redirect(url_for("warehouse"))
        
        wb = load_workbook(file)
        sheet = wb.active
        count = 0
        
        # Supponendo che le colonne siano: A=Seriale, B=Codice, C=Descrizione
        for row in sheet.iter_rows(min_row=2, values_only=True): 
            if row[0]: # Se c'è un seriale
                existing = WarehouseItem.query.filter_by(serial=str(row[0])).first()
                if not existing:
                    new_item = WarehouseItem(serial=str(row[0]), code=str(row[1]), description=str(row[2]), status="generale")
                    db.session.add(new_item)
                    count += 1
        
        db.session.commit()
        flash(f"Importati {count} nuovi seriali da Excel.", "success")
        return redirect(url_for("warehouse"))

    # --- VISTE E SCHEDE ---

    @app.route("/warehouse")
    @login_required
    def warehouse():
        items_generale = WarehouseItem.query.filter_by(status="generale").all()
        technicians = Technician.query.all()
        return render_template("warehouse.html", items=items_generale, technicians=technicians)

    @app.route("/technicians")
    @login_required
    def technicians():
        return render_template("technicians.html", technicians=Technician.query.all())

    @app.route("/technician/<int:tech_id>")
    @login_required
    def technician_detail(tech_id):
        tech = Technician.query.get_or_404(tech_id)
        mobile_items = WarehouseItem.query.filter_by(assigned_to=tech_id, status="in_viaggio").all()
        certs = Certificate.query.filter_by(technician_id=tech.id).all()
        return render_template("technician_detail.html", tech=tech, mobile_items=mobile_items, certs=certs)

app = create_app()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
