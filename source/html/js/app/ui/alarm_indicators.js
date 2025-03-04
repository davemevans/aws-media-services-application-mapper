/*! Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
       SPDX-License-Identifier: Apache-2.0 */

import * as model from "../model.js";
import * as alarms from "../alarms.js";
import * as diagrams from "./diagrams.js";

const updateAlarmState = function (     // NOSONAR
    current_alarming_subscribers,
    previous_alarming_subscribers
) {
    // iterate through current 'set' alerts
    const alarming_nodes = [];
    const inactive_nodes = [];
    for (let subscriber of current_alarming_subscribers) {
        let node = model.nodes.get(subscriber.ResourceArn);
        if (node) {
            node.alarming = true;
            // track which nodes are signaling an alert
            if (!alarming_nodes.includes(subscriber.ResourceArn)) {
                alarming_nodes.push(subscriber.ResourceArn);
                let selected = node.render.alert_selected();
                let unselected = node.render.alert_unselected();
                // only update the node if the SVG changes
                if (
                    selected != node.image.selected ||
                    unselected != node.image.unselected
                ) {
                    node.image.selected = selected;
                    node.image.unselected = unselected;
                    model.nodes.update(node);
                    let matches = diagrams.have_all([node.id]);
                    for (let diagram of matches) {
                        diagram.nodes.update(node);
                        diagram.alert(true);
                    }
                }
            }
        }
    }

    // calculate the current alerts not included in the previous alerts
    for (let subscriber of previous_alarming_subscribers) {
        let found = false;
        for (let node_id of alarming_nodes) {
            found = found || node_id == subscriber.ResourceArn;
        }
        if (!found) {
            inactive_nodes.push(subscriber.ResourceArn);
        }
    }

    // 'unalert' the nodes that are no longer alerting
    for (let node_id of inactive_nodes) {
        let node = model.nodes.get(node_id);
        if (node) {
            node.alarming = false;
            // only switch the node render if the node is neither alarming nor alerting
            let selected = node.render.normal_selected();
            let unselected = node.render.normal_unselected();
            if (
                selected != node.image.selected ||
                unselected != node.image.unselected
            ) {
                node.image.selected = selected;
                node.image.unselected = unselected;
                model.nodes.update(node);
                let matches = diagrams.have_all([node.id]);
                for (let diagram of matches) {
                    diagram.nodes.update(node);
                    diagram.alert(false);
                }
            }
        }
    }
};

alarms.add_callback(updateAlarmState);
