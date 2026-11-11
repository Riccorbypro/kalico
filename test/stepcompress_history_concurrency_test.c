#define _GNU_SOURCE

#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>

#include "list.h"
#include "stepcompress.h"
#include "msgblock.h"

#define INITIAL_MARKERS 256
#define WRITER_ITERATIONS 20000
#define READER_ITERATIONS 100000
#define HISTORY_WINDOW 64
#define CLOCK_BASE 100000

struct race_context {
    struct stepcompress *sc;
    pthread_barrier_t start;
    atomic_int failures;
};

static void
record_failure(struct race_context *ctx, const char *message)
{
    if (atomic_fetch_add_explicit(&ctx->failures, 1, memory_order_relaxed)
        == 0)
        fprintf(stderr, "concurrent history failure: %s\n", message);
}

static int
valid_position(int64_t position)
{
    return position >= 0 && position <= WRITER_ITERATIONS;
}

static int
valid_history_record(const struct pull_history_steps *record)
{
    return record->first_clock <= record->last_clock
        && record->first_clock >= CLOCK_BASE
        && record->last_clock <= CLOCK_BASE + WRITER_ITERATIONS * 4
        && valid_position(record->start_position)
        && record->step_count == 0
        && record->interval == 0
        && record->add == 0;
}

static void *
writer_thread(void *data)
{
    struct race_context *ctx = data;
    pthread_barrier_wait(&ctx->start);

    for (int i = 1; i <= WRITER_ITERATIONS; i++) {
        uint64_t clock = CLOCK_BASE + (uint64_t)i * 4;
        if (stepcompress_set_last_position(ctx->sc, clock, i))
            record_failure(ctx, "set_last_position returned an error");
        stepcompress_history_expire(ctx->sc, clock - HISTORY_WINDOW);
        if (!(i & 15))
            sched_yield();
    }
    return NULL;
}

static void *
reader_thread(void *data)
{
    struct race_context *ctx = data;
    pthread_barrier_wait(&ctx->start);

    for (int i = 0; i < READER_ITERATIONS; i++) {
        uint64_t clock = CLOCK_BASE
            + (uint64_t)(i % (WRITER_ITERATIONS + 1)) * 4 + 1;
        int64_t position = stepcompress_find_past_position(ctx->sc, clock);
        if (!valid_position(position))
            record_failure(ctx, "find_past_position returned an invalid position");

        struct pull_history_steps records[16];
        int count = stepcompress_extract_old(
            ctx->sc, records, 16, clock - 200, clock + 200);
        if (count < 0 || count > 16)
            record_failure(ctx, "extract_old returned an invalid count");
        for (int j = 0; j < count; j++) {
            if (!valid_history_record(&records[j]))
                record_failure(ctx, "extract_old returned a corrupt history record");
        }
        if (!(i & 31))
            sched_yield();
    }
    return NULL;
}

static int
check_position_history_contract(void)
{
    struct list_head message_queue;
    list_init(&message_queue);
    struct stepcompress *sc = stepcompress_alloc(&message_queue);
    if (!sc)
        return 1;

    stepcompress_set_last_position(sc, 100, 10);
    stepcompress_set_last_position(sc, 200, 20);
    if (stepcompress_find_past_position(sc, 50) != 10
        || stepcompress_find_past_position(sc, 150) != 10
        || stepcompress_find_past_position(sc, 250) != 20) {
        fprintf(stderr, "position history contract failed\n");
        stepcompress_free(sc);
        return 1;
    }

    struct pull_history_steps records[2];
    int count = stepcompress_extract_old(sc, records, 2, 50, 250);
    if (count != 2 || records[0].first_clock != 200
        || records[0].start_position != 20
        || records[1].first_clock != 100
        || records[1].start_position != 10) {
        fprintf(stderr, "history extraction contract failed\n");
        stepcompress_free(sc);
        return 1;
    }
    stepcompress_free(sc);
    return 0;
}

// Exercise snapshot-based interpolation on actual compressed steps, including
// changing intervals and negative motion, not just zero-step position markers.
static int
check_compressed_step_positions(void)
{
    struct list_head messages;
    list_init(&messages);
    struct stepcompress *sc = stepcompress_alloc(&messages);
    stepcompress_fill(sc, 1, 0, 1, 2);
    stepcompress_set_time(sc, 0., 1000.);
    const double times[] = {.100, .210, .330, .460};
    const uint64_t queries[] = {1000, 1150, 1250, 1350, 1490};
    const int64_t positions[] = {0, 1, 2, 3, 4};
    int failed = 0;
    for (int direction = 1; direction >= 0; direction--) {
        double print_time = direction ? 1. : 2.;
        for (int i = 0; i < 4; i++)
            failed |= stepcompress_append(sc, direction, print_time, times[i]);
        failed |= stepcompress_flush(sc, UINT64_MAX);
        for (int i = 0; i < 5; i++) {
            uint64_t clock = queries[i] + (direction ? 0 : 1000);
            int64_t expected = direction ? positions[i] : 4 - positions[i];
            int64_t actual = stepcompress_find_past_position(sc, clock);
            if (actual != expected) {
                fprintf(stderr, "compressed position at %lu: got %ld, expected %ld\n",
                        (unsigned long)clock, (long)actual, (long)expected);
                failed = 1;
            }
        }
    }
    stepcompress_free(sc);
    message_queue_free(&messages);
    return failed;
}

int
main(void)
{
    if (check_position_history_contract())
        return 1;
    if (check_compressed_step_positions())
        return 1;

    struct list_head message_queue;
    list_init(&message_queue);
    struct stepcompress *sc = stepcompress_alloc(&message_queue);
    if (!sc)
        return 1;
    for (int i = 0; i < INITIAL_MARKERS; i++)
        stepcompress_set_last_position(sc, CLOCK_BASE - 2000 + i * 4, i);

    struct race_context ctx = {
        .sc = sc,
        .failures = 0,
    };
    pthread_barrier_init(&ctx.start, NULL, 2);
    pthread_t writer, reader;
    pthread_create(&writer, NULL, writer_thread, &ctx);
    pthread_create(&reader, NULL, reader_thread, &ctx);
    pthread_join(writer, NULL);
    pthread_join(reader, NULL);
    pthread_barrier_destroy(&ctx.start);

    int failures = atomic_load_explicit(&ctx.failures, memory_order_relaxed);
    stepcompress_free(sc);
    if (failures)
        return 1;

    puts("stepcompress history concurrency and position checks passed");
    return 0;
}

void
errorf(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    fputc('\n', stderr);
    va_end(args);
}
