-- 写入权持有者续期 lock/chunks/meta；模型长时间生成但正文延迟输出时，append 不会发生，
-- 由周期性的写入权检查续期。
-- KEYS: lock, chunks, meta
-- ARGV: task_id, stream_ttl
-- 返回: 1 仍持有并已续期；0 已失去写入权

local task_id = ARGV[1]
if redis.call("GET", KEYS[1]) ~= task_id then
    return 0
end
local ttl = tonumber(ARGV[2])
redis.call("EXPIRE", KEYS[1], ttl)
if redis.call("HGET", KEYS[3], "task_id") == task_id then
    redis.call("EXPIRE", KEYS[2], ttl)
    redis.call("EXPIRE", KEYS[3], ttl)
end
return 1
