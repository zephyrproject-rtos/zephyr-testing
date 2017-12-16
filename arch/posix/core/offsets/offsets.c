/*
 * Copyright (c) 2010-2014 Wind River Systems, Inc.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * @file
 * @brief Kernel structure member offset definition file
 *
 * This module is responsible for the generation of the absolute symbols whose
 * value represents the member offsets for various IA-32 structures.
 *
 * All of the absolute symbols defined by this module will be present in the
 * final kernel ELF image (due to the linker's reference to the _OffsetAbsSyms
 * symbol).
 *
 * INTERNAL
 * It is NOT necessary to define the offset for every member of a structure.
 * Typically, only those members that are accessed by assembly language routines
 * are defined; however, it doesn't hurt to define all fields for the sake of
 * completeness.
 */

#include <gen_offset.h> /* located in kernel/include */

/* list of headers that define whose structure offsets will be generated */

#include <kernel_structs.h>

#include <kernel_offsets.h>

#ifdef CONFIG_DEBUG_INFO
GEN_OFFSET_SYM(_kernel_arch_t, isf);
#endif

#ifdef CONFIG_GDB_INFO
GEN_OFFSET_SYM(_thread_arch_t, esf);
#endif

#if (defined(CONFIG_FP_SHARING) || defined(CONFIG_GDB_INFO))
GEN_OFFSET_SYM(_thread_arch_t, excNestCount);
#endif



GEN_ABS_SYM_END
