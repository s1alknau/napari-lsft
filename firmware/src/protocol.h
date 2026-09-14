// UC2-REST serial protocol.
//
// The host (UC2-REST, and through it ImSwitch and napari-lsft) sends one
// compact JSON object per line, terminated by '\n', carrying a "task" naming
// the endpoint and a "qid" that the reply has to echo back.
//
// A reply is framed by two marker lines:
//
//     ++
//     {"qid":7,"return":1}
//     --
//
// Two rules follow from how the host parses this stream (mserial.py,
// _process_commands) and are easy to break by accident:
//
//   * a line is only collected while a block is open, so anything printed
//     outside ++/-- is harmless -- except that
//   * any line containing the substring "error" aborts the pending command,
//     and a line that is exactly "reboot" makes the host drop it. So refusals
//     are reported as {"return":0}, never as prose.
//
// Blocking moves expect one reply per moving axis *in addition* to the
// immediate acknowledgement (see Motor.move_stepper: nResponses = axes + 1),
// which is why completions are reported separately as the axes finish.
#pragma once

#include <Arduino.h>

void protocolBegin();

// Feed the serial input and emit any pending completion messages.
void protocolPoll();
