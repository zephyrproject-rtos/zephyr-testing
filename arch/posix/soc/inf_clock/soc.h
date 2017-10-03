/*
 * Copyright (c) 2017 Oticon A/S
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef _POSIX_SOC_INF_CLOCK_H
#define _POSIX_SOC_INF_CLOCK_H

#include "board_soc.h"

#ifdef __cplusplus
extern "C" {
#endif

void posix_soc_halt_cpu(void);
void posix_soc_interrupt_raised(void);
void posix_soc_boot_cpu(void);
void posix_soc_atomic_halt_cpu(unsigned int imask);

#ifdef __cplusplus
}
#endif

#endif /* _POSIX_SOC_INF_CLOCK_H */
