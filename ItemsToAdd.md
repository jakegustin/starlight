- Need to clarify evaluation mechanisms!
  - Key point: Latency vs Accuracy trade-off: Do we emphasize more rapid detection at the cost of possible misfires, or emphasize confirmation the user is in the zone at the cost of slower reaction times?

- Future work to be done in this project: Deterministic Replay of real-world tests

- BLE + Wifi may be problematic for ESP32 receivers as they are both operating at 2.4GHz frequency
  - Ironically, a wired connection gives more freedom for controller placement: keeps the ESP32 container design more straightforward
  - Can consider an evaluation of Wifi based vs Serial based controller communications, particularly with respect to reliability of a connection in terms of packet drop

- Another possible direction: Semi-automatic configuration of receivers via a software tool: Indicate position of each receiver in the GUI and estimated distance between each receiver.
  - Receive configuration details via Serial/Wifi
  - Much more realistic to implement vs fine-tuning every ESP32 in the real world.

- The demo assumes ideal conditions to demonstrate effectiveness: 3 ESP32 receivers in a straight line in an environment with minimal external wireless interference
  - Almost certain this project will not be "production-ready" by the end of the semester: A deep dive into limitations, their impacts, and possible solutions to be discussed in final report.
  - MUST mention multipathing concerns! Not fully solved with current design!

- Consider an evaluation of ratcheting vs naive thresholds
  - Evaluate with respect to accurate zone detection and the number of "lighting oscillations"

- Agentic AI should easily be to create a web dashboard for controller data to be displayed with: a likely addition for the demo.

- With new additions, previously stated focus of UUID rotation may need to be deprioritized to meet more essential requirements.

