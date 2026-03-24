def admin_users_key():
    return "admin:users:list"

def admin_deposits_key():
    return "admin:deposits:list"

def admin_profile_key(user_id):
    return f"admin:profile:{user_id}"

def admin_dashboard_stats_key():
    return "admin:dashboard:stats"

def user_profile_key(user_id):
    return f"user:profile:{user_id}"

def user_transactions_key(user_id):
    return f"user:transactions:{user_id}"

def user_summary_key(user_id):
    return f"user:summary:{user_id}"