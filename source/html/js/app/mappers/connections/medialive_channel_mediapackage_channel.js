/*! Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
       SPDX-License-Identifier: Apache-2.0 */

import * as server from "../../server.js";
import * as connections from "../../connections.js";

export const update = () => {
    const current = connections.get_current();
    const url = current[0];
    const api_key = current[1];
    const items = [];
    return new Promise((resolve) => {   // NOSONAR
        server
            .get(
                `${url}/cached/medialive-channel-mediapackage-channel`,
                api_key
            )
            .then((results) => {
                for (let connection of results) {
                    const data = JSON.parse(connection.data);
                    const options = {
                        id: connection.arn,
                        to: connection.to,
                        from: connection.from,
                        data: data,
                        label: "HLS",
                        arrows: "to",
                        color: { color: "black" },
                        dashes: false,
                    };
                    const hasMoreConnections = _.filter(
                        results,
                        (function (local_connection) {
                            return function (o) {
                                if (
                                    o.from === local_connection.from &&
                                    o.to === local_connection.to
                                ) {
                                    let shouldEndWith = "0";
                                    if (local_connection.arn.endsWith("0"))
                                        shouldEndWith = "1";
                                    if (o.arn.endsWith(shouldEndWith))
                                        return true;
                                }
                                return false;
                            };
                        })(connection)
                    );

                    if (hasMoreConnections.length) {
                        /** curve it */
                        options.smooth = { enabled: true };
                        options.smooth.type = "discrete";

                        if (_.has(data, "pipeline")) {
                            options.label += ` ${data.pipeline}`;
                            options.smooth.type =
                                data.pipeline === 1 ? "curvedCCW" : "curvedCW";
                            options.smooth.roundness = 0.15;
                        }
                    }
                    items.push(options);
                }
                resolve(items);
            });
    });
};

export const module_name = "MediaLive Channel to MediaPackage Channel";
