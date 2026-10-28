# Parking Management System

Software Project Management – Innovative Assignment
Nirma University, Institute of Technology

Submitted by: Suhani Chaplot (Roll No. 23BEC189)
Submitted to: Daiwat Vyas Sir

## About the project

This is a parking management system for one parking area. The operator adds a vehicle when it comes, the system gives a slot and an entry ticket, and when the vehicle leaves it calculates the fee and gives a receipt. The administrator can see the reports and change the rates.

It is made in Python with Flask and SQLite, using the incremental model in a plan of 16 weeks.

## Modules

| Module | What it does |
|---|---|
| Login | Admin and operator login, passwords are stored in hashed form |
| Slots | Shows free and filled slots, with an overstay alert |
| Entry | Gives a slot and an entry ticket |
| Exit and fee | Calculates the fee and gives an exit receipt |
| Reports | Usage and income report (admin only) |
| Settings | Change rates, add slots and add users (admin only) |

## How to run

1. Install Python and then Flask: `pip install flask`
2. Run the project: `python app.py`
3. Open `http://127.0.0.1:5000` in the browser

Sample logins:

- Administrator: `admin` / `admin123`
- Operator: `operator` / `op123`

The file `parking.db` is made automatically on the first run.

## Files

| File | Use |
|---|---|
| app.py | The Flask application with all the modules |
| requirements.txt | Packages needed to run the project |
| .gitignore | Files not saved in Git (parking.db and __pycache__) |
| README.md | This file |

## Tutorials done

| Tutorial | Topic |
|---|---|
| 1 to 4 | Early planning of the project |
| 5 | CPM Network Diagram |
| 6 | Process and Project Metrics |
| 7 | Software Effort Estimation (LOC, Function Point, Basic COCOMO) |
| 8 | Object Oriented Metrics and Make-Buy Decision (Use Case Points) |
| 9 | Risk Management |
| 10 | Project Monitoring and Tracking |
| 11 | Software Project Audit |
| 12 | Quality Assurance and Quality Control |
| 13 | Software Maintenance Management |
| 14 | Software Configuration Management |
| 15 | Integrated Project Case Study |

## Branches and versions

- `main` has the stable code and the released versions
- `develop` joins the finished work of all modules
- `feature/...` branches are for the modules
- Versions: v0.1.0 (login, slots, entry), v0.2.0 (exit, fee, reports), v1.0.0 (first release)