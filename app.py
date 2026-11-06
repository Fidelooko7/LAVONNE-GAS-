from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, date, timedelta
from pathlib import Path
import sqlite3, os, secrets

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'instance' / 'lavonne_v3.db'
UPLOADS = ROOT / 'static' / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
SECRET_FILE = DB.parent / 'secret_key'
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_urlsafe(48))
app.secret_key = os.environ.get('LAVONNE_SECRET') or SECRET_FILE.read_text().strip()
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
ALLOWED = {'png','jpg','jpeg','webp'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','employee')), active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, brand TEXT NOT NULL, size TEXT NOT NULL, sell_price REAL NOT NULL DEFAULT 0, buy_cost REAL NOT NULL DEFAULT 0, image TEXT, active INTEGER DEFAULT 1, UNIQUE(brand,size));
CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL UNIQUE REFERENCES products(id), full_qty INTEGER NOT NULL DEFAULT 0, empty_qty INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), customer_id INTEGER REFERENCES customers(id), qty INTEGER NOT NULL, unit_price REAL NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, paid REAL NOT NULL, payment_method TEXT NOT NULL, sale_date TEXT NOT NULL, user_id INTEGER REFERENCES users(id), note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS debts(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), sale_id INTEGER REFERENCES sales(id), original REAL NOT NULL, due_date TEXT NOT NULL, issued_at TEXT NOT NULL, note TEXT DEFAULT '', created_by INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS debt_payments(id INTEGER PRIMARY KEY, debt_id INTEGER NOT NULL REFERENCES debts(id), amount REAL NOT NULL, method TEXT NOT NULL, paid_at TEXT NOT NULL, recorded_by INTEGER REFERENCES users(id), note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), qty INTEGER NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, purchase_date TEXT NOT NULL, supplier TEXT DEFAULT '', user_id INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, title TEXT NOT NULL, amount REAL NOT NULL, expense_date TEXT NOT NULL, note TEXT DEFAULT '', user_id INTEGER REFERENCES users(id));
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), customer_name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT DEFAULT '', qty INTEGER NOT NULL, unit_price REAL NOT NULL, fulfillment TEXT NOT NULL, payment_choice TEXT NOT NULL, payment_status TEXT NOT NULL DEFAULT 'unverified', order_status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, handled_by INTEGER REFERENCES users(id), delivered_by INTEGER REFERENCES users(id), delivery_note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
'''
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init_db():
    c=db(); c.executescript(SCHEMA); c.commit(); c.close()
init_db()

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def today(): return date.today().isoformat()
def money(v): return round(float(v or 0),2)
def audit(action, details):
    if session.get('uid'):
        c=db(); c.execute('INSERT INTO audit(user_id,action,details,created_at) VALUES(?,?,?,?)',(session['uid'],action,details,now())); c.commit(); c.close()
def current_user():
    if not session.get('uid'): return None
    c=db(); u=c.execute('SELECT * FROM users WHERE id=? AND active=1',(session['uid'],)).fetchone(); c.close(); return u
@app.context_processor
def inject():
    return {'me':current_user(),'today':today(),'money':lambda x:f'{money(x):,.2f}'}
def login_required(f):
    @wraps(f)
    def w(*a,**k):
        if not current_user(): return redirect(url_for('login',next=request.path))
        return f(*a,**k)
    return w
def admin_required(f):
    @wraps(f)
    def w(*a,**k):
        u=current_user()
        if not u: return redirect(url_for('login'))
        if u['role']!='admin': abort(403)
        return f(*a,**k)
    return w
def get_products(active_only=False):
    c=db(); q='SELECT p.*, COALESCE(s.full_qty,0) full_qty, COALESCE(s.empty_qty,0) empty_qty FROM products p LEFT JOIN stock s ON s.product_id=p.id'
    if active_only:q+=' WHERE p.active=1'
    rows=c.execute(q+' ORDER BY p.brand,p.size').fetchall(); c.close(); return rows
def balance(c, debt_id):
    r=c.execute('SELECT d.original-COALESCE(SUM(p.amount),0) bal FROM debts d LEFT JOIN debt_payments p ON p.debt_id=d.id WHERE d.id=? GROUP BY d.id',(debt_id,)).fetchone(); return money(r['bal'] if r else 0)

@app.route('/')
def home():
    products=get_products(True)
    return render_template('store.html',products=products)
@app.route('/order',methods=['POST'])
def place_order():
    c=db(); p=c.execute('SELECT * FROM products WHERE id=? AND active=1',(request.form.get('product_id'),)).fetchone()
    try: qty=max(1,int(request.form.get('qty','1')))
    except: qty=1
    if not p: flash('Please select an available product.','error'); return redirect(url_for('home'))
    name=request.form.get('name','').strip(); phone=request.form.get('phone','').strip()
    if not name or not phone: flash('Name and phone number are required.','error'); return redirect(url_for('home'))
    c.execute('INSERT INTO orders(product_id,customer_name,phone,address,qty,unit_price,fulfillment,payment_choice,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(p['id'],name,phone,request.form.get('address','').strip(),qty,p['sell_price'],request.form.get('fulfillment','delivery'),request.form.get('payment_choice','pay_on_delivery'),now()))
    c.commit(); oid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.close(); return render_template('order_done.html',oid=oid)
@app.route('/setup',methods=['GET','POST'])
def setup():
    c=db(); exists=c.execute('SELECT COUNT(*) n FROM users').fetchone()['n']; c.close()
    if exists: return redirect(url_for('login'))
    if request.method=='POST':
        username=request.form.get('username','').strip(); password=request.form.get('password','')
        if len(username)<3 or len(password)<8: flash('Use a username of at least 3 characters and password of at least 8.','error')
        else:
            c=db(); c.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)',(username,generate_password_hash(password),'admin',now())); c.commit(); c.close(); flash('Administrator created. Sign in.','success'); return redirect(url_for('login'))
    return render_template('setup.html')
@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        c=db(); u=c.execute('SELECT * FROM users WHERE username=? AND active=1',(request.form.get('username','').strip(),)).fetchone(); c.close()
        if u and check_password_hash(u['password_hash'],request.form.get('password','')):
            session.clear(); session['uid']=u['id']; audit('login','Signed in'); return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Incorrect username or password.','error')
    return render_template('login.html')
@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

@app.route('/dashboard')
@login_required
def dashboard():
    selected=request.args.get('date',today())
    try: datetime.strptime(selected,'%Y-%m-%d')
    except: selected=today()
    c=db()
    sales=c.execute('SELECT COALESCE(SUM(total),0) gross,COALESCE(SUM(paid),0) collected,COALESCE(SUM(total-paid),0) credit,COALESCE(SUM(qty),0) units,COALESCE(SUM((unit_price-unit_cost)*qty),0) gross_profit FROM sales WHERE substr(sale_date,1,10)=?',(selected,)).fetchone()
    expenses=c.execute('SELECT COALESCE(SUM(amount),0) total FROM expenses WHERE expense_date=?',(selected,)).fetchone()['total']
    stock=c.execute('SELECT COALESCE(SUM(full_qty),0) fulls,COALESCE(SUM(empty_qty),0) empties FROM stock').fetchone()
    debt=c.execute('SELECT COALESCE(SUM(d.original-COALESCE(p.paid,0)),0) bal FROM debts d LEFT JOIN (SELECT debt_id,SUM(amount) paid FROM debt_payments GROUP BY debt_id) p ON p.debt_id=d.id').fetchone()['bal']
    recent=c.execute('SELECT s.*,p.brand,p.size FROM sales s JOIN products p ON p.id=s.product_id ORDER BY s.id DESC LIMIT 7').fetchall()
    orders=c.execute("SELECT COUNT(*) n FROM orders WHERE order_status IN ('new','awaiting_payment','authorised','out_for_delivery')").fetchone()['n']
    c.close(); gp=money(sales['gross_profit']); return render_template('dashboard.html',selected=selected,sales=sales,expenses=expenses,net=gp-money(expenses),stock=stock,debt=debt,recent=recent,orders=orders)
@app.route('/products',methods=['GET','POST'])
@admin_required
def products_page():
    if request.method=='POST':
        brand=request.form.get('brand','').strip(); size=request.form.get('size','').strip()
        try: price=money(request.form.get('sell_price')); cost=money(request.form.get('buy_cost'))
        except: price=cost=0
        if not brand or size not in ('6kg','13kg'): flash('Enter a brand and valid cylinder size.','error')
        else:
            c=db(); c.execute('INSERT INTO products(brand,size,sell_price,buy_cost) VALUES(?,?,?,?) ON CONFLICT(brand,size) DO UPDATE SET sell_price=excluded.sell_price,buy_cost=excluded.buy_cost,active=1',(brand,size,price,cost)); pid=c.execute('SELECT id FROM products WHERE brand=? AND size=?',(brand,size)).fetchone()['id']; c.execute('INSERT OR IGNORE INTO stock(product_id) VALUES(?)',(pid,)); c.commit(); c.close(); audit('product','Added/updated '+brand+' '+size); flash('Product saved.','success'); return redirect(url_for('products_page'))
    return render_template('products.html',products=get_products())
@app.route('/products/<int:pid>/image',methods=['POST'])
@admin_required
def product_image(pid):
    f=request.files.get('photo')
    if not f or not f.filename: flash('Choose an image first.','error'); return redirect(url_for('products_page'))
    ext=f.filename.rsplit('.',1)[-1].lower()
    if ext not in ALLOWED: flash('Use JPG, PNG or WEBP images.','error'); return redirect(url_for('products_page'))
    filename=f'product_{pid}_{secrets.token_hex(5)}.{ext}'; f.save(UPLOADS/filename)
    c=db(); c.execute('UPDATE products SET image=? WHERE id=?',(filename,pid)); c.commit(); c.close(); flash('Product photo updated.','success'); return redirect(url_for('products_page'))
@app.route('/inventory',methods=['GET','POST'])
@login_required
def inventory():
    if request.method=='POST':
        pid=int(request.form['product_id']); full=int(request.form.get('full_qty',0)); empty=int(request.form.get('empty_qty',0))
        c=db(); c.execute('UPDATE stock SET full_qty=MAX(0,full_qty+?),empty_qty=MAX(0,empty_qty+?) WHERE product_id=?',(full,empty,pid)); c.commit(); c.close(); audit('stock adjustment',f'Product {pid}: full {full}, empty {empty}'); flash('Stock adjusted.','success'); return redirect(url_for('inventory'))
    return render_template('inventory.html',products=get_products())
@app.route('/sales',methods=['GET','POST'])
@login_required
def sales():
    c=db()
    if request.method=='POST':
        pid=int(request.form['product_id']); qty=max(1,int(request.form['qty'])); price=money(request.form['unit_price']); paid=money(request.form['paid']); method=request.form.get('method','cash'); cid=request.form.get('customer_id') or None
        p=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone(); st=c.execute('SELECT * FROM stock WHERE product_id=?',(pid,)).fetchone()
        if not p or not st or st['full_qty']<qty: flash('Not enough full cylinders in stock.','error')
        elif paid<0 or paid>price*qty: flash('Amount paid must be between zero and the sale total.','error')
        elif paid<price*qty and not cid: flash('Select a registered customer for a credit balance.','error')
        else:
            total=money(price*qty); c.execute('INSERT INTO sales(product_id,customer_id,qty,unit_price,unit_cost,total,paid,payment_method,sale_date,user_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(pid,cid,qty,price,p['buy_cost'],total,paid,method,now(),session['uid']))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('UPDATE stock SET full_qty=full_qty-?,empty_qty=empty_qty+? WHERE product_id=?',(qty,qty,pid))
            if paid<total: c.execute('INSERT INTO debts(customer_id,sale_id,original,due_date,issued_at,created_by) VALUES(?,?,?,?,?,?)',(cid,sid,total-paid,request.form.get('due_date') or today(),now(),session['uid']))
            c.commit(); audit('sale',f'Sale #{sid}, total KSh {total}'); flash('Sale recorded and stock updated.','success'); c.close(); return redirect(url_for('sales'))
    products=get_products(); customers=c.execute('SELECT * FROM customers ORDER BY name').fetchall(); rows=c.execute('SELECT s.*,p.brand,p.size,cu.name customer FROM sales s JOIN products p ON p.id=s.product_id LEFT JOIN customers cu ON cu.id=s.customer_id ORDER BY s.id DESC LIMIT 30').fetchall(); c.close(); return render_template('sales.html',products=products,customers=customers,rows=rows)
@app.route('/customers',methods=['GET','POST'])
@login_required
def customers():
    c=db()
    if request.method=='POST':
        c.execute('INSERT INTO customers(name,phone,address,created_at) VALUES(?,?,?,?)',(request.form['name'].strip(),request.form['phone'].strip(),request.form.get('address',''),now())); c.commit(); flash('Customer added.','success'); c.close(); return redirect(url_for('customers'))
    rows=c.execute('SELECT cu.*,COALESCE(SUM(d.original),0)-COALESCE((SELECT SUM(dp.amount) FROM debt_payments dp JOIN debts dx ON dx.id=dp.debt_id WHERE dx.customer_id=cu.id),0) balance FROM customers cu LEFT JOIN debts d ON d.customer_id=cu.id GROUP BY cu.id ORDER BY cu.name').fetchall(); c.close(); return render_template('customers.html',customers=rows)
@app.route('/debts')
@login_required
def debts():
    c=db(); rows=c.execute('SELECT d.*,cu.name,cu.phone,COALESCE(SUM(dp.amount),0) paid FROM debts d JOIN customers cu ON cu.id=d.customer_id LEFT JOIN debt_payments dp ON dp.debt_id=d.id GROUP BY d.id ORDER BY d.due_date').fetchall(); payments=c.execute('SELECT dp.*,cu.name customer,d.id debt_id FROM debt_payments dp JOIN debts d ON d.id=dp.debt_id JOIN customers cu ON cu.id=d.customer_id ORDER BY dp.id DESC LIMIT 100').fetchall(); c.close(); items=[]
    for r in rows:
        x=dict(r); x['balance']=money(x['original']-x['paid']); x['status']='PAID' if x['balance']<=0 else ('OVERDUE' if x['due_date']<today() else ('DUE SOON' if x['due_date']<= (date.today()+timedelta(days=3)).isoformat() else 'OPEN')); items.append(x)
    return render_template('debts.html',debts=items,payments=payments)
@app.route('/debts/<int:did>/payment',methods=['POST'])
@login_required
def debt_payment(did):
    amount=money(request.form.get('amount')); c=db(); bal=balance(c,did)
    if amount<=0 or amount>bal: flash('Payment must be greater than zero and not exceed the balance.','error')
    else:
        c.execute('INSERT INTO debt_payments(debt_id,amount,method,paid_at,recorded_by,note) VALUES(?,?,?,?,?,?)',(did,amount,request.form.get('method','cash'),now(),session['uid'],request.form.get('note',''))); c.commit(); flash('Debt payment recorded.','success')
    c.close(); return redirect(url_for('debts'))
@app.route('/purchases',methods=['GET','POST'])
@admin_required
def purchases():
    c=db()
    if request.method=='POST':
        pid=int(request.form['product_id']); qty=max(1,int(request.form['qty'])); cost=money(request.form['unit_cost']); d=request.form.get('purchase_date') or today(); total=money(qty*cost)
        c.execute('INSERT INTO purchases(product_id,qty,unit_cost,total,purchase_date,supplier,user_id) VALUES(?,?,?,?,?,?,?)',(pid,qty,cost,total,d,request.form.get('supplier',''),session['uid'])); c.execute('UPDATE stock SET full_qty=full_qty+? WHERE product_id=?',(qty,pid)); c.execute('UPDATE products SET buy_cost=? WHERE id=?',(cost,pid)); c.commit(); c.close(); flash('Purchase saved and stock increased.','success'); return redirect(url_for('purchases'))
    prods=get_products(); rows=c.execute('SELECT pu.*,p.brand,p.size FROM purchases pu JOIN products p ON p.id=pu.product_id ORDER BY pu.id DESC LIMIT 30').fetchall(); c.close(); return render_template('purchases.html',products=prods,rows=rows)
@app.route('/expenses',methods=['GET','POST'])
@admin_required
def expenses():
    c=db()
    if request.method=='POST':
        c.execute('INSERT INTO expenses(title,amount,expense_date,note,user_id) VALUES(?,?,?,?,?)',(request.form['title'],money(request.form['amount']),request.form.get('expense_date') or today(),request.form.get('note',''),session['uid'])); c.commit(); c.close(); flash('Expense recorded.','success'); return redirect(url_for('expenses'))
    rows=c.execute('SELECT * FROM expenses ORDER BY id DESC LIMIT 40').fetchall(); c.close(); return render_template('expenses.html',rows=rows)
@app.route('/orders')
@login_required
def orders():
    c=db(); rows=c.execute('SELECT o.*,p.brand,p.size FROM orders o JOIN products p ON p.id=o.product_id ORDER BY o.id DESC').fetchall(); c.close(); return render_template('orders.html',rows=rows)
@app.route('/orders/<int:oid>/update',methods=['POST'])
@login_required
def update_order(oid):
    c=db(); o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if not o: c.close(); abort(404)
    if o['order_status']=='delivered':
        c.close(); flash('This order is already completed. Contact the administrator if a correction is needed.','error'); return redirect(url_for('orders'))
    pay=request.form.get('payment_status',o['payment_status']); status=request.form.get('order_status',o['order_status'])
    allowed_pay={'unverified','verified','pledged'}; allowed_status={'new','awaiting_payment','authorised','out_for_delivery','delivered','failed','customer_unavailable','returned'}
    if pay not in allowed_pay or status not in allowed_status: c.close(); abort(400)
    if status in ('authorised','out_for_delivery','delivered') and pay not in ('verified','pledged'): flash('Verify payment or record an approved pledge before dispatch.','error')
    elif status=='delivered' and o['order_status']!='delivered':
        st=c.execute('SELECT full_qty FROM stock WHERE product_id=?',(o['product_id'],)).fetchone()
        if not st or st['full_qty']<o['qty']: flash('Insufficient stock to complete delivery.','error')
        else:
            c.execute('UPDATE stock SET full_qty=full_qty-?,empty_qty=empty_qty+? WHERE product_id=?',(o['qty'],o['qty'],o['product_id']))
            customer=c.execute('SELECT id FROM customers WHERE phone=?',(o['phone'],)).fetchone()
            if not customer:
                c.execute('INSERT INTO customers(name,phone,address,created_at) VALUES(?,?,?,?)',(o['customer_name'],o['phone'],o['address'],now())); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            else: cid=customer['id']
            paid=money(o['unit_price']*o['qty']) if pay=='verified' else 0
            prod=c.execute('SELECT buy_cost FROM products WHERE id=?',(o['product_id'],)).fetchone()
            c.execute('INSERT INTO sales(product_id,customer_id,qty,unit_price,unit_cost,total,paid,payment_method,sale_date,user_id,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(o['product_id'],cid,o['qty'],o['unit_price'],prod['buy_cost'],money(o['unit_price']*o['qty']),paid,'order',now(),session['uid'],'Customer order #'+str(oid)))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            if pay=='pledged': c.execute('INSERT INTO debts(customer_id,sale_id,original,due_date,issued_at,created_by,note) VALUES(?,?,?,?,?,?,?)',(cid,sid,money(o['unit_price']*o['qty']),today(),now(),session['uid'],'Order pledge #'+str(oid)))
            c.execute('UPDATE orders SET payment_status=?,order_status=?,handled_by=?,delivered_by=?,delivery_note=? WHERE id=?',(pay,status,session['uid'],session['uid'],request.form.get('delivery_note',''),oid)); c.commit(); c.close(); flash('Delivery completed; sale and stock have been updated.','success'); return redirect(url_for('orders'))
    else:
        c.execute('UPDATE orders SET payment_status=?,order_status=?,handled_by=?,delivery_note=? WHERE id=?',(pay,status,session['uid'],request.form.get('delivery_note',''),oid)); c.commit(); flash('Order updated.','success')
    c.close(); return redirect(url_for('orders'))
@app.route('/reports')
@login_required
def reports():
    start=request.args.get('start',today()); end=request.args.get('end',today())
    c=db(); rows=c.execute('SELECT p.brand,p.size,SUM(s.qty) qty,SUM(s.total) sales,SUM((s.unit_price-s.unit_cost)*s.qty) profit FROM sales s JOIN products p ON p.id=s.product_id WHERE date(s.sale_date) BETWEEN ? AND ? GROUP BY p.id ORDER BY sales DESC',(start,end)).fetchall()
    total=c.execute('SELECT COALESCE(SUM(total),0) sales,COALESCE(SUM(paid),0) collected,COALESCE(SUM((unit_price-unit_cost)*qty),0) gross_profit FROM sales WHERE date(sale_date) BETWEEN ? AND ?',(start,end)).fetchone()['sales']; collected=c.execute('SELECT COALESCE(SUM(paid),0) n FROM sales WHERE date(sale_date) BETWEEN ? AND ?',(start,end)).fetchone()['n']; gp=c.execute('SELECT COALESCE(SUM((unit_price-unit_cost)*qty),0) n FROM sales WHERE date(sale_date) BETWEEN ? AND ?',(start,end)).fetchone()['n']; exp=c.execute('SELECT COALESCE(SUM(amount),0) n FROM expenses WHERE expense_date BETWEEN ? AND ?',(start,end)).fetchone()['n']; stock=c.execute('SELECT COALESCE(SUM(full_qty),0) fulls,COALESCE(SUM(empty_qty),0) empties FROM stock').fetchone(); c.close()
    return render_template('reports.html',start=start,end=end,rows=rows,total=total,collected=collected,gp=gp,expenses=exp,net=money(gp-exp),stock=stock)
@app.route('/team',methods=['GET','POST'])
@admin_required
def team():
    c=db()
    if request.method=='POST':
        username=request.form['username'].strip(); password=request.form['password']
        if len(password)<8: flash('Password must be at least 8 characters.','error')
        else:
            try: c.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)',(username,generate_password_hash(password),'employee',now())); c.commit(); flash('Employee account created.','success')
            except sqlite3.IntegrityError: flash('That username already exists.','error')
    rows=c.execute('SELECT id,username,role,active,created_at FROM users ORDER BY id').fetchall(); c.close(); return render_template('team.html',users=rows)
@app.route('/team/<int:uid>/toggle',methods=['POST'])
@admin_required
def toggle_user(uid):
    if uid==session['uid']: flash('You cannot disable your own account.','error')
    else:
        c=db(); c.execute('UPDATE users SET active=1-active WHERE id=?',(uid,)); c.commit(); c.close(); flash('Account status changed.','success')
    return redirect(url_for('team'))
@app.route('/account/password',methods=['POST'])
@login_required
def password_change():
    old=request.form.get('old',''); new=request.form.get('new',''); c=db(); u=c.execute('SELECT * FROM users WHERE id=?',(session['uid'],)).fetchone()
    if not check_password_hash(u['password_hash'],old) or len(new)<8: flash('Current password is incorrect or new password is too short.','error')
    else: c.execute('UPDATE users SET password_hash=? WHERE id=?',(generate_password_hash(new),session['uid'])); c.commit(); flash('Password changed.','success')
    c.close(); return redirect(url_for('dashboard'))
@app.errorhandler(403)
def forbidden(e): return render_template('message.html',heading='Access restricted',message='This section is available to the administrator only.'),403
if __name__=='__main__': app.run(host='127.0.0.1',port=int(os.environ.get('PORT', '5001')),debug=False)
