/* Bounded, allocation-free PostgreSQL binary COPY framing validator. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>

typedef struct {
    uint64_t rows;
    uint32_t columns, fields, remaining, row_bytes;
    unsigned stage, used;
    unsigned char scratch[19];
} gm_counter;

static const unsigned char header[19] = {
    'P','G','C','O','P','Y','\n',255,'\r','\n',0,0,0,0,0,0,0,0,0
};

size_t gm_counter_size(void) { return sizeof(gm_counter); }

int gm_counter_init(void *state, unsigned columns) {
    if (!state || !columns || columns > 32767) return 1;
    memset(state, 0, sizeof(gm_counter));
    ((gm_counter *)state)->columns = columns;
    return 0;
}

static int field_done(gm_counter *s) {
    if (!s->fields) return 1;
    if (--s->fields) s->stage = 2;
    else {
        if (s->rows == UINT64_MAX) return 1;
        s->rows++;
        s->stage = 1;
    }
    return 0;
}

static int process(void *state, const unsigned char *data, size_t size,
                   size_t *consumed, uint32_t *ends, size_t capacity, size_t *count) {
    gm_counter *s = state;
    if (!s || (!data && size) || s->stage > 4 || !consumed || !count ||
        (ends && (!capacity || size > UINT32_MAX))) return 1;
    size_t pos = 0;
    *consumed = 0;
    *count = 0;
    while (pos < size) {
        if (s->stage == 4) goto bad;
        if (s->stage == 3) {
            size_t take = size-pos < s->remaining ? size-pos : s->remaining;
            pos += take;
            s->remaining -= (uint32_t)take;
            if (!s->remaining) {
                uint64_t previous = s->rows;
                if (field_done(s)) goto bad;
                if (s->rows != previous && ends) {
                    ends[(*count)++] = (uint32_t)pos;
                    if (*count == capacity) break;
                }
            }
            continue;
        }
        unsigned want = s->stage == 0 ? 19 : s->stage == 1 ? 2 : 4;
        if (s->used >= want) goto bad;
        size_t take = size-pos < want-s->used ? size-pos : want-s->used;
        memcpy(s->scratch+s->used, data+pos, take);
        s->used += (unsigned)take;
        pos += take;
        if (s->used < want) continue;
        s->used = 0;
        if (s->stage == 0) {
            if (memcmp(s->scratch, header, 19)) goto bad;
            s->stage = 1;
        } else if (s->stage == 1) {
            unsigned count = ((unsigned)s->scratch[0]<<8) | s->scratch[1];
            if (count == 65535) s->stage = 4;
            else {
                if (count != s->columns) goto bad;
                s->fields = count;
                s->row_bytes = 2;
                s->stage = 2;
            }
        } else {
            uint32_t length = ((uint32_t)s->scratch[0]<<24) |
                ((uint32_t)s->scratch[1]<<16) |
                ((uint32_t)s->scratch[2]<<8) | s->scratch[3];
            if (length == UINT32_MAX) length = 0;
            if (length > 1048576 || s->row_bytes > 1048576-4 ||
                length > 1048576-4-s->row_bytes) goto bad;
            s->row_bytes += 4+length;
            s->remaining = length;
            if (length) s->stage = 3;
            else {
                uint64_t previous = s->rows;
                if (field_done(s)) goto bad;
                if (s->rows != previous && ends) {
                    ends[(*count)++] = (uint32_t)pos;
                    if (*count == capacity) break;
                }
            }
        }
    }
    *consumed = pos;
    return 0;
bad:
    s->stage = 5;
    return 1;
}

int gm_counter_feed(void *state, const unsigned char *data, size_t size) {
    size_t consumed, count;
    return process(state, data, size, &consumed, NULL, 0, &count);
}

/* Row ends are offsets within this input block. No output past capacity. */
int gm_counter_scan_rows(void *state, const unsigned char *data, size_t size,
                         size_t *consumed, uint32_t *ends, size_t capacity,
                         size_t *count) {
    if (!ends || !capacity || capacity > 20000 || size > 2 * 1024 * 1024) return 1;
    return process(state, data, size, consumed, ends, capacity, count);
}

int gm_counter_finish(void *state, uint64_t *rows) {
    gm_counter *s = state;
    if (!s || !rows || s->stage != 4 || s->used || s->remaining) return 1;
    *rows = s->rows;
    return 0;
}
