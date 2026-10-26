-- 仅允许当前 task 向 streaming 状态的 Stream 追加 entry，并原子续期 lock/chunks/meta。
-- lock 用短 TTL（进程退出后很快释放），chunks/meta 用覆盖完整运行的 TTL。
-- KEYS: lock, chunks, meta
-- ARGV: task_id, stream_ttl, lock_ttl, field_1, value_1, ...
-- 返回: {1, entry_id} 成功；{0, "ownership_lost"} 已失去写入权

local task_id = ARGV[1]
if redis.call("GET", KEYS[1]) ~= task_id then
    return {0, "ownership_lost"}
end
if redis.call("HGET", KEYS[3], "task_id") ~= task_id then
    return {0, "ownership_lost"}
end
if redis.call("HGET", KEYS[3], "status") ~= "streaming" then
    return {0, "ownership_lost"}
end

local fields = {}
for index = 4, #ARGV do
    table.insert(fields, ARGV[index])
end
local entry_id = redis.call("XADD", KEYS[2], "*", unpack(fields))
local ttl = tonumber(ARGV[2])
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[3]))
redis.call("EXPIRE", KEYS[2], ttl)
redis.call("EXPIRE", KEYS[3], ttl)
return {1, entry_id}
