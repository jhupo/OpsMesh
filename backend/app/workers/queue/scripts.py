ENQUEUE_SCRIPT = """
if ARGV[3] == "1" then
    redis.call("SET", KEYS[1], ARGV[1], "EX", ARGV[4])
    redis.call("RPUSH", KEYS[2], ARGV[2])
    return 1
end
local reserved = redis.call("SET", KEYS[1], ARGV[1], "NX", "EX", ARGV[4])
if not reserved then
    return 0
end
redis.call("RPUSH", KEYS[2], ARGV[2])
return 1
"""

LEASE_JOB_SCRIPT = """
local removed = redis.call("LREM", KEYS[1], 1, ARGV[1])
if removed == 0 then
    return 0
end
redis.call("ZADD", KEYS[2], ARGV[2], ARGV[3])
return 1
"""
