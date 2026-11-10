-- SPDX-License-Identifier: MIT
-- STX-W-observed Shiteng RS485 protocol reference. Lua 5.1 compatible.
-- Pure byte processing: no hardware, OS, network or vendor SDK dependencies.
-- All byte arrays are dense ONE-based tables of integers in 0..255.
-- Frame: 7E TYPE LEN_H LEN_L PAYLOAD CRC_H CRC_L 0D 0A; LEN excludes CRLF.
-- CRC-16/MODBUS arithmetic, HIGH byte first on this proprietary (non-Modbus) wire.
-- Evidence includes labeled original-panel actions and associated main-unit
-- traffic. A matching reply does not establish physical execution. See
-- docs/protocol.md and docs/compatibility.md for scope and unresolved meanings.
local Shiteng = {}
local MAX_FRAME_BYTES = 512
Shiteng.MAX_FRAME_BYTES = MAX_FRAME_BYTES

local function integer_in(value, low, high)
    return type(value) == "number" and value >= low and value <= high
        and value == math.floor(value)
end

local function check_bytes(bytes, name)
    assert(type(bytes) == "table", (name or "bytes") .. " must be a byte array")
    local count, length = 0, #bytes
    for index, value in pairs(bytes) do
        assert(integer_in(index, 1, length), "byte array must be dense and one-based")
        assert(integer_in(value, 0, 255), "byte values must be integers in 0..255")
        count = count + 1
    end
    assert(count == length, "byte array must be dense and one-based")
    return length
end

local function copy_range(bytes, first, last)
    local result = {}
    for index = first, last do
        result[#result + 1] = bytes[index]
    end
    return result
end

local function xor(left, right)
    local value, bit = 0, 1
    while left > 0 or right > 0 do
        if left % 2 ~= right % 2 then value = value + bit end
        left = math.floor(left / 2)
        right = math.floor(right / 2)
        bit = bit * 2
    end
    return value
end

local function crc16(bytes, last)
    local crc = 65535
    for index = 1, last do
        crc = xor(crc, bytes[index])
        for _ = 1, 8 do
            local odd = crc % 2 == 1
            crc = math.floor(crc / 2)
            if odd then crc = xor(crc, 40961) end
        end
    end
    return crc
end

function Shiteng.encode(kind, payload)
    assert(integer_in(kind, 0, 255), "frame type must be an integer in 0..255")
    local size = check_bytes(payload, "payload")
    assert(size + 8 <= MAX_FRAME_BYTES, "frame exceeds supported 512-byte limit")
    local declared = size + 6
    local frame = {126, kind, math.floor(declared / 256), declared % 256}
    for index = 1, size do frame[#frame + 1] = payload[index] end
    local crc = crc16(frame, #frame)
    frame[#frame + 1] = math.floor(crc / 256)
    frame[#frame + 1] = crc % 256
    frame[#frame + 1] = 13
    frame[#frame + 1] = 10
    return frame
end

-- Decoder preserves arbitrary chunk boundaries and length-delimited payloads.
-- Unknown frame types are returned unchanged after framing/CRC validation.
-- At most 511 incomplete bytes are retained between calls. A plausible but
-- incomplete length header is deliberately not skipped merely because a later
-- 7E occurs inside it: that could be legitimate payload. Call reset() only at a
-- caller-established stream discontinuity/timeout to discard a truncated frame.
function Shiteng.decoder_new()
    local buffer = {}
    local decoder = {}

    function decoder:reset()
        buffer = {}
    end

    function decoder:buffered_bytes()
        return #buffer
    end

    local function drain(frames)
        while #buffer > 0 do
            if buffer[1] ~= 126 then
                table.remove(buffer, 1)
            elseif #buffer < 4 then
                return
            else
                local declared = buffer[3] * 256 + buffer[4]
                local total = declared + 2
                if declared < 6 or total > MAX_FRAME_BYTES then
                    table.remove(buffer, 1)
                elseif #buffer < total then
                    return
                else
                    local valid_tail = buffer[total - 1] == 13 and buffer[total] == 10
                    local wire_crc = buffer[declared - 1] * 256 + buffer[declared]
                    if valid_tail and crc16(buffer, declared - 2) == wire_crc then
                        frames[#frames + 1] = {
                            type = buffer[2],
                            payload = copy_range(buffer, 5, declared - 2),
                            raw = copy_range(buffer, 1, total)
                        }
                        buffer = copy_range(buffer, total + 1, #buffer)
                    else
                        table.remove(buffer, 1)
                    end
                end
            end
        end
    end

    function decoder:feed(bytes)
        -- Validate before changing buffered state, including rejected input tails.
        local count = check_bytes(bytes)
        local frames = {}
        for index = 1, count do
            buffer[#buffer + 1] = bytes[index]
            drain(frames)
        end
        return frames
    end

    return decoder
end

local CONTROL_FIELDS = {power = true, auto = true, fan = true, mode = true}

local function check_changes(changes, complete)
    assert(type(changes) == "table", "control state must be a table")
    for field in pairs(changes) do
        assert(CONTROL_FIELDS[field], "unknown control field: " .. tostring(field))
    end
    for _, field in ipairs({"power", "auto"}) do
        if complete or changes[field] ~= nil then
            assert(type(changes[field]) == "boolean", field .. " must be an explicit boolean")
        end
    end
    if complete or changes.fan ~= nil then
        assert(integer_in(changes.fan, 1, 3), "only observed fan settings 1..3 are supported")
    end
    if complete or changes.mode ~= nil then
        assert(integer_in(changes.mode, 0, 3), "mode must be an integer in 0..3")
    end
end

local function set_bit(value, bit, enabled)
    local previous = math.floor(value / bit) % 2
    return value - previous * bit + (enabled and bit or 0)
end

-- Update only the observed bits. This does not claim the fan field is exactly
-- two bits wide: unobserved bits are preserved, as are the opaque tail bytes.
-- Mode writes also set/clear p1 bit2 to match observed final mode states; its
-- physical meaning is unknown and transition timing remains unimplemented.
function Shiteng.patch_control(payload, changes)
    assert(check_bytes(payload, "control payload") == 4, "control payload must have four bytes")
    check_changes(changes, false)
    local result = copy_range(payload, 1, 4)
    if changes.power ~= nil then result[1] = set_bit(result[1], 128, changes.power) end
    if changes.auto ~= nil then result[1] = set_bit(result[1], 64, changes.auto) end
    if changes.fan ~= nil then
        result[2] = result[2] - math.floor(result[2] / 32) % 4 * 32 + changes.fan * 32
    end
    if changes.mode ~= nil then
        result[1] = result[1] - result[1] % 4 + changes.mode
        result[2] = set_bit(result[2], 4, changes.mode == 0 or changes.mode == 3)
    end
    return result
end

-- Produces a four-byte PAYLOAD, not a complete frame. Combine with encode(0x81,...).
-- 0x14,0x00 are fixed observed opaque bytes, not named protocol fields.
-- Combinations beyond captured golden cases are inferred from independent bits;
-- encoding does not establish execution or ACK semantics. Automatic speed
-- policy, transition scheduling and retries are not implemented here.
function Shiteng.make_control(state)
    check_changes(state, true)
    return Shiteng.patch_control({0, 0, 20, 0}, state)
end

function Shiteng.encode_humidity(value)
    -- 30..90 is a library policy, NOT a proven protocol or device range.
    -- Only 55 and 60 have independently labeled original-panel captures.
    assert(integer_in(value, 30, 90), "humidity must be an integer in the library range 30..90")
    return Shiteng.encode(196, {0, value})
end

function Shiteng.heartbeat()
    -- Observed original-panel interval: about 3.2 seconds. This helper only
    -- encodes bytes; exact poll/reply semantics remain incomplete. No timer.
    return Shiteng.encode(32, {0})
end

return Shiteng
