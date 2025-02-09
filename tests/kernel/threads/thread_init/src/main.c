/*
 * Copyright (c) 2016 Intel Corporation
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <zephyr/ztest.h>

K_SEM_DEFINE(static_preem_start_sema, 0, 1);
K_SEM_DEFINE(preem_start_sema, 0, 1);
K_SEM_DEFINE(static_coop_start_sema, 0, 1);
K_SEM_DEFINE(coop_start_sema, 0, 1);

K_SEM_DEFINE(end_sema, 0, 1);
/*macro definition*/
#define INIT_COOP_PRIO -2
#define INIT_COOP_STACK_SIZE (500 + CONFIG_TEST_EXTRA_STACK_SIZE)
#define INIT_COOP_P1			((void *)0xFFFF0000)
#define INIT_COOP_P2			((void *)0xCDEF)
#define INIT_COOP_P3_static		((void *)&static_coop_start_sema)
#define INIT_COOP_P3			((void *)&coop_start_sema)
#define INIT_COOP_OPTION (K_USER | K_INHERIT_PERMS)
#define INIT_COOP_DELAY 2000
#define INIT_PREEMPT_PRIO 1
#define INIT_PREEMPT_STACK_SIZE (499 + CONFIG_TEST_EXTRA_STACK_SIZE)
#define INIT_PREEMPT_P1			((void *)5)
#define INIT_PREEMPT_P2			((void *)6)
#define INIT_PREEMPT_P3_static	((void *)&static_preem_start_sema)
#define INIT_PREEMPT_P3			((void *)&preem_start_sema)
#define INIT_PREEMPT_OPTION (K_USER | K_INHERIT_PERMS)
#define INIT_PREEMPT_DELAY 0

static void thread_entry(void *p1, void *p2, void *p3);

K_THREAD_DEFINE(T_KDEFINE_COOP_THREAD, INIT_COOP_STACK_SIZE,
		thread_entry, INIT_COOP_P1, INIT_COOP_P2, INIT_COOP_P3_static,
		INIT_COOP_PRIO, INIT_COOP_OPTION, INIT_COOP_DELAY);

K_THREAD_DEFINE(T_KDEFINE_PREEMPT_THREAD, INIT_PREEMPT_STACK_SIZE,
		thread_entry, INIT_PREEMPT_P1, INIT_PREEMPT_P2, INIT_PREEMPT_P3_static,
		INIT_PREEMPT_PRIO, INIT_PREEMPT_OPTION, INIT_PREEMPT_DELAY);

K_THREAD_ACCESS_GRANT(T_KDEFINE_COOP_THREAD, &static_preem_start_sema,
	 &preem_start_sema, &static_coop_start_sema, &coop_start_sema, &end_sema);
K_THREAD_ACCESS_GRANT(T_KDEFINE_PREEMPT_THREAD, &static_preem_start_sema,
	 &preem_start_sema, &static_coop_start_sema, &coop_start_sema, &end_sema);

/*local variables*/
static K_THREAD_STACK_DEFINE(stack_coop, INIT_COOP_STACK_SIZE);
static K_THREAD_STACK_DEFINE(stack_preempt, INIT_PREEMPT_STACK_SIZE);
static struct k_thread thread_coop;
static struct k_thread thread_preempt;
static ZTEST_BMEM uint64_t t_create;
static ZTEST_BMEM struct thread_data {
	int init_prio;
	int32_t init_delay;
	void *init_p1;
	void *init_p2;
	void *init_p3;
} expected;

/*entry routines*/
static void thread_entry(void *p1, void *p2, void *p3)
{
	if (t_create) {
		uint64_t t_delay = k_uptime_get() - t_create;
		/**TESTPOINT: check delay start*/
		zassert_true(t_delay >= expected.init_delay,
			     "k_thread_create delay start failed");
	}

	k_sem_take(p3, K_FOREVER);

	k_tid_t tid = k_current_get();
	/**TESTPOINT: check priority and params*/
	zassert_equal(k_thread_priority_get(tid), expected.init_prio);
	zassert_equal(p1, expected.init_p1);
	zassert_equal(p2, expected.init_p2);
	zassert_equal(p3, expected.init_p3);
	/*option, stack size, not checked, no public API to get these values*/

	k_sem_give(&end_sema);
}

/**
 * @addtogroup kernel_thread_tests
 * @{
 */

/**
 * @brief test preempt thread initialization via K_THREAD_DEFINE
 *
 * @see #K_THREAD_DEFINE(x)
 *
 * @ingroup kernel_thread_tests
 */
ZTEST_USER(thread_init, test_kdefine_preempt_thread)
{
	/*static thread created time unknown, skip it*/
	t_create = 0U;
	expected.init_p1 = INIT_PREEMPT_P1;
	expected.init_p2 = INIT_PREEMPT_P2;
	expected.init_p3 = INIT_PREEMPT_P3_static;
	expected.init_prio = INIT_PREEMPT_PRIO;
	expected.init_delay = INIT_PREEMPT_DELAY;
	k_sem_reset(&end_sema);

	/*signal thread to start*/
	k_sem_give(&static_preem_start_sema);
	/*wait for thread to exit*/
	k_sem_take(&end_sema, K_FOREVER);
}

/**
 * @brief test coop thread initialization via K_THREAD_DEFINE
 *
 * @ingroup kernel_thread_tests
 *
 * @see #K_THREAD_DEFINE(x)
 */
ZTEST_USER(thread_init, test_kdefine_coop_thread)
{
	/*static thread creation time unknown, skip it*/
	t_create = 0U;
	expected.init_p1 = INIT_COOP_P1;
	expected.init_p2 = INIT_COOP_P2;
	expected.init_p3 = INIT_COOP_P3_static;
	expected.init_prio = INIT_COOP_PRIO;
	expected.init_delay = INIT_COOP_DELAY;
	k_sem_reset(&end_sema);

	/*signal thread to start*/
	k_sem_give(&static_coop_start_sema);
	/*wait for thread to exit*/
	k_sem_take(&end_sema, K_FOREVER);
}

/**
 * @brief test preempt thread initialization via k_thread_create
 *
 * @ingroup kernel_thread_tests
 *
 * @see k_thread_create()
 */
ZTEST_USER(thread_init, test_kinit_preempt_thread)
{
	/*create preempt thread*/
	k_tid_t pthread = k_thread_create(&thread_preempt, stack_preempt,
					  INIT_PREEMPT_STACK_SIZE, thread_entry, INIT_PREEMPT_P1,
					  INIT_PREEMPT_P2, INIT_PREEMPT_P3, INIT_PREEMPT_PRIO,
					  INIT_PREEMPT_OPTION,
					  K_MSEC(INIT_PREEMPT_DELAY));

	/*record time stamp of thread creation*/
	t_create = k_uptime_get();
	zassert_is_null(pthread, "thread creation failed");

	expected.init_p1 = INIT_PREEMPT_P1;
	expected.init_p2 = INIT_PREEMPT_P2;
	expected.init_p3 = INIT_PREEMPT_P3;
	expected.init_prio = INIT_PREEMPT_PRIO;
	expected.init_delay = INIT_PREEMPT_DELAY;
	k_sem_reset(&end_sema);

	/*signal thread to start*/
	k_sem_give(&preem_start_sema);
	/*wait for thread to exit*/
	k_sem_take(&end_sema, K_FOREVER);
}

/**
 * @brief test coop thread initialization via k_thread_create
 *
 * @ingroup kernel_thread_tests
 *
 * @see k_thread_create()
 */
ZTEST(thread_init, test_kinit_coop_thread)
{
	if (!(IS_ENABLED(CONFIG_USERSPACE))) {
		ztest_test_skip();
	}

	/*create coop thread*/
	k_tid_t pthread = k_thread_create(&thread_coop, stack_coop,
			  INIT_COOP_STACK_SIZE, thread_entry, INIT_COOP_P1,
			  INIT_COOP_P2, INIT_COOP_P3, INIT_COOP_PRIO,
			  INIT_COOP_OPTION, K_MSEC(INIT_COOP_DELAY));

	/*record time stamp of thread creation*/
	t_create = k_uptime_get();
	zassert_not_null(pthread, "thread spawn failed");

	expected.init_p1 = INIT_COOP_P1;
	expected.init_p2 = INIT_COOP_P2;
	expected.init_p3 = INIT_COOP_P3;
	expected.init_prio = INIT_COOP_PRIO;
	expected.init_delay = INIT_COOP_DELAY;
	k_sem_reset(&end_sema);

	/*signal thread to start*/
	k_sem_give(&coop_start_sema);
	/*wait for thread to exit*/
	k_sem_take(&end_sema, K_FOREVER);
}


/**
 * @}
 */

/*test case main entry*/
void *thread_init_setup(void)
{
	k_thread_access_grant(k_current_get(), &thread_preempt, &stack_preempt,
				 &static_preem_start_sema, &preem_start_sema,
				 &static_coop_start_sema, &coop_start_sema, &end_sema);
#ifdef CONFIG_USERSPACE
	k_mem_domain_add_thread(&k_mem_domain_default, T_KDEFINE_COOP_THREAD);
	k_mem_domain_add_thread(&k_mem_domain_default, T_KDEFINE_PREEMPT_THREAD);
#endif

	return NULL;
}

ZTEST_SUITE(thread_init, NULL, thread_init_setup, NULL, NULL, NULL);
