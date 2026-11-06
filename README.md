# Lavonne Gas — Version 3 (fresh build)

A new Flask + SQLite business manager and public customer catalogue. This is a separate project; it does not overwrite or import Version 1/2 data.

## Included
- Public catalogue with product photos, configurable prices, and customer order form.
- First-run administrator setup and username/password login.
- Administrator-created employee accounts (employee accounts cannot access buying costs, product pricing controls, purchases, expenses, team management, or profit figures).
- Product catalogue for gas brand + 6kg/13kg, adjustable selling price and buying cost, image uploads from phone gallery.
- Full/empty inventory, purchase records and stock adjustment log entries.
- Sales with per-transaction selling price, payment collected, customer credit and due date.
- Customer directory, debt balances and separately recorded partial repayments.
- Customer order processing: orders do not change stock until marked delivered; dispatch requires verified payment or a pledged/credit status.
- Dashboard active-date filter, sales totals, money collected, stock counts and outstanding debt.
- Date-range reports with gross/net profit for administrator.

## Install in Termux (Android)
1. Extract the ZIP into a new folder, not over your old project.
2. In Termux:

```bash
pkg update && pkg install python unzip -y
mkdir -p ~/projects/lavonne_v3
unzip ~/storage/downloads/Lavonne_Gas_V3.zip -d ~/projects/lavonne_v3
cd ~/projects/lavonne_v3/Lavonne_Gas_V3
pip install -r requirements.txt
PORT=5001 python app.py
```

3. Open `http://127.0.0.1:5001/setup` in Chrome and create the administrator account.
4. Sign in, add products, set buying and selling prices, add stock, and create employee accounts.
5. Employees sign in at `/login`. Customers use the public home page `/`.

## Important operating notes
- The app listens on localhost only. It is not publicly hosted and is not indexed by Google. Public deployment needs a domain, HTTPS, persistent hosting, secure secret configuration, and production hardening.
- This is a new database at `instance/lavonne_v3.db`; the old Version 1/2 database is untouched.
- Make regular backups of the `instance` folder and `static/uploads` folder.
- Profit accuracy depends on entering the correct buying cost for each product and keeping it current. The sale stores a snapshot of the product's current buying cost and the actual selling price used.
- Sales are counted separately from cash collected. Customer credit is tracked as a debt.
- For an online launch, add CSRF protection, secure cookie settings, rate limiting, HTTPS-only deployment, automated backups, and independent security testing before using real customer data.
- This starter release does not yet have M-Pesa API integration, SMS notifications, supplier account balances, or automatic Google indexing.
