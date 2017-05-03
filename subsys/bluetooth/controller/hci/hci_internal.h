/*
 * Copyright (c) 2016 Nordic Semiconductor ASA
 * Copyright (c) 2016 Vinayak Kariappa Chettimada
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef _HCI_CONTROLLER_H_
#define _HCI_CONTROLLER_H_

#if defined(CONFIG_BLUETOOTH_CONTROLLER_TO_HOST_FC)
extern s32_t    hci_hbuf_total;
extern u32_t    hci_hbuf_sent;
extern u32_t    hci_hbuf_acked;
extern atomic_t hci_state_mask;

#define HCI_STATE_BIT_RESET 0
#endif

#define HCI_CLASS_EVT_REQUIRED    0
#define HCI_CLASS_EVT_DISCARDABLE 1
#define HCI_CLASS_EVT_CONNECTION  2
#define HCI_CLASS_ACL_DATA        3

void hci_init(struct k_poll_signal *signal_host_buf);
struct net_buf *hci_cmd_handle(struct net_buf *cmd);
void hci_evt_encode(struct radio_pdu_node_rx *node_rx, struct net_buf *buf);
s8_t hci_get_class(struct radio_pdu_node_rx *node_rx);
#if defined(CONFIG_BLUETOOTH_CONN)
int hci_acl_handle(struct net_buf *acl);
void hci_acl_encode(struct radio_pdu_node_rx *node_rx, struct net_buf *buf);
void hci_num_cmplt_encode(struct net_buf *buf, u16_t handle, u8_t num);
#endif
#endif /* _HCI_CONTROLLER_H_ */
