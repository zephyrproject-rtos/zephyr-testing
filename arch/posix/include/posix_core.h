/*
 * Copyright (c) 2017 Oticon A/S
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef _POSIX_CORE_H
#define _POSIX_CORE_H

#include "kernel.h"

#ifdef __cplusplus
extern "C" {
#endif


typedef struct {
	k_thread_entry_t entry_point;
	void *arg1;
	void *arg2;
	void *arg3;

	int thread_idx;

#if defined(CONFIG_ARCH_HAS_THREAD_ABORT)
	/* The kernel may indicate that a thread has been aborted several */
	/* times */
	int aborted;
#endif
} posix_thread_status_t;


void pc_new_thread(posix_thread_status_t *ptr);
void pc_swap(int next_allowed_thread_nbr, int this_thread_nbr);
void pc_main_thread_start(int next_allowed_thread_nbr);
void pc_init_multithreading(void);
void pc_clean_up(void);

void pc_new_thread_pre_start(void); /*defined in thread.c*/

#ifdef __cplusplus
}
#endif

#endif /* _POSIX_CORE_H */
