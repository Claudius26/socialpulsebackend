def admin_users_key():
    return "admin:users:list"

def admin_deposits_key():
    return "admin:deposits:list"

def admin_profile_key(user_id):
    return f"admin:profile:{user_id}"

def user_detail_key(user_id):
    return f"admin:user:{user_id}"

def dashboard_stats_key():
    return "admin:dashboard:stats"