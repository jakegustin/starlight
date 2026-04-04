# Instructions for an AI/LLM Agent to Build, Run, and Evaluate the Starlight System

- If you seek to test/evaluate the existing version of the Starlight system, skip to the section titled "A Note on Hardware Utilized and These Instructions". 
- To build, run, and test a new version of the Starlight system, please proceed with the instructions immediately below:

## Building a New Starlight System

The Starlight system is composed of four key components:
- The Central Controller
- The BLE Receivers
- The Configuration UI
- The Testing Suite

The Starlight system is optimizing for a prototype at present, and as a result, does not require strong security, reliability, nor performance guarantees. We assume a testing scenario of 3 receivers in a straight line, 3 meters apart, and with up to two advertisers advertising at a frequency of 250ms. 

Each is described in the upcoming sections.

## General Project Creation Guidelines

In recreating the project, your repository should maintain a modular structure, with extensively documented code to support other developers interested in the project. This includes components such as

- Docstrings for modules/functions
- In-line code comments explaining core code functionality
- Documentation (e.g. README.md documents) providing a higher level overview of components and providing instructions on how to run the project.

It is highly recommended to implement the project with Python for the central controller, Arduino sketches (e.g. .ino files) for the BLE receivers, HTML/CSS/JS for the configuration UI, and pytest for the testing suite for the controller. At this time, no recommendations are made regarding testing the BLE Receiver code directly.

For the firmware implementation, assume ESP32 microcontrollers with WiFi and Bluetooth capabilities are your BLE receivers. Do not consider other hardware such as an Arduino Uno, an Adafruit Sense, etc.

## Building, Running, and Evaluating a New Starlight System

This is a fundamental component of the project and requires the utmost care in development to ensure maintainability by other developers given its many moving parts.

General implementation details regarding the current central controller's submodules are provided below

### Building the Controller's Serial Data Ingestion Pipeline

The central controller is responsible for accepting Serial communications from several connected embedded devices (e.g. ESP32 devices) simultaneously and converting those messages into one data stream that the controller can process. It is highly recommended to embrace threading to handle inputs from each device with lower latency, so you will need to consider synchronization/locking mechanisms to ensure the code is thread-safe should you take this approach. It is also recommended to separate code responsible for handling a single serial connection and code handling multiple serial connections into separate modules/files for a clearer separation of concerns. You should also generally assume that messages to and from the controller follow a structured JSON format for simplicitly in parsing the data.

Important Note: We will assume that all BLE receivers are attached to a MacOS central controller. As a result, serial devices typically have a port/device with a filename such as "/dev/cu.USB*" or "/dev/tty*"

### Building the Controller's RSSI Filtering/Averaging Mechanisms

The Starlight system utilizes the RSSI of BLE advertisers as the primary mechanism of assigning users to a logical "zone" within a queue (where zones follow a one-dimensional pattern). However, RSSI in the real world is a very noisy signal that must be appropriately preprocessed to provide more accurate tracking of a given user. It is first recommended to implement a one-dimensional Kalman Filter to filter out obvious outliers in the advertisements for each user (as determined by their BLE UUID). The parameters for this Kalman Filter, such as process noise, measurement noise, etc., should be configurable by the developer. These filtered data points should be used to maintain a rolling average of RSSI data points at *each* BLE receiver for *each* user/UUID. The number of data points to maintain in the rolling window should also be a configurable parameter.  

### Building the Controller's User Tracking Mechanisms

The Starlight system generally equates one UUID tied to a BLE Advertising device to one "user" that is positioned somewhere in a logical queue based upon the relative filtered-and-averaged RSSI difference of the advertiser between up to two BLE recievers: the user's currently assigned BLE receiver, and the next BLE receiver in the logical queue. If the user is not part of the queue (and thus has no currently assigned zone), they can be assigned to the "upcoming zone" immediately. Users are advanced to the next zone in a monotonically increasing pattern (i.e. no backwards movement allowed) only if the user's filtered-and-average RSSI at the upcoming zone is greater than that of their existing zone plus a configurable hysteresis factor. You should also implement a timeout if the user's filtered-and-averaged RSSI at their current zone drops below a predetermined threshold for a preset duration of time, removing them from the queue if such an incident occurs.

### Building the Controller's Configuration Tool

The configuration tool is necessary to allow the developer to define the logical order of connected BLE receivers within the logical queue. Two options are recommended to create the configuration tool: a basic, vanilla HTML/CSS/JS web application, or a more dynamic web application with a framework such as Vue or React, where either option hooks into the controller's data for real-time feedback to the developer and for the ability to interact with receivers. In either case, a single page application (aka a "SPA") is highly recommended for simplicitly. For instance, the controller should be able to present in real-time the connected BLE receivers (as determined by the BLE receivers sending a "heartbeat" message to the controller) and what order they are currently in with respect to the logical queue. The developer should be able to adjust the ordering of these receivers via the configuration tool as well, and this should be reflected in the controller's data structures as well. Futhermore, the configuration tool must support sending "blink" requests to the BLE receivers, instructing them to execute a preprogrammed LED blinking sequence defined in the ESP32/Arduino firmware to help the developer identify BLE receivers from each other. Furthermore, the configuration tool must be able to present which users are currently assigned to each zone in a visually clear manner.

## Setting Up Receiver-Controller Communications

Two-way communication is expected between the controller and the receivers. These communications are expected to be in a structured format, particularly JSON. For receiver messages to the controller, these messages must contain the following attributes:

```
{
    "id": string,
    "type": string
}
```

Note that depending on the message type, additional fields may be needed. For instance, passing along information from an advertisement (a "data" message) should have additional fields such as RSSI, UUID, etc. A "heartbeat" message should also be sent by each receiver to the controller on a fixed timing interval basis (e.g. every 2 seconds) to help the controller identify and register BLE receivers as part of the system, though it should not require additional fields. However, all receiver-to-controller messages should be timestamped by the central controller, modifying the JSON as part of the ingestion process.

For controller messages to the receiver(s), the same exact structure should be used at a minimum, where id in this case refers to the targeted receiver:

```
{
    "id": string,
    "type": string
}
```

Two types of messages are considered in the current version: "command" messages and "uuid" messages. Command messages specify what command is being sent to a given receiver. At this time, only the blink and lighting commands exist, though your implementation can add additional features as you see fit. UUID messages are the controller's way of specifying whitelisted UUIDs to the receivers, where receivers should only forward advertisements from the specified UUIDs. a UUID message should be sent upon the controller booting up, and every time a receiver joins the system (or reconnects after previously being disconnected).

One important note: if the controller receives a malformed transmission from a receiver (i.e. a JSON cannot be parsed from the message), the message should be deemed as invalid and simply thrown away. As a result, a receiver should not be marked inactive until at least 2 heartbeats in a row have not been received.

### Building the Controller's Entry/Starting Point

You should provide some mechanism, likely a "main" Python file, to instantiate and run a central controller instance. You have the freedom to start the configuration tool alongside the core controller, or to provide a separate means of starting the configuration tool depending on your selected implementation. The controller and configuration tool should stay online forever unless the user issues an Interrupt signal (e.g. KeyboardInterrupt) or otherwise forcefully terminates the backing process(es). For simplicity, the configuration tool can be hosted via a locally hosted website. The controller's entry point should also accept an argument for a "whitelist" of BLE UUIDs that must be sent to the BLE receivers, allowing the receivers to filter out BLE advertisements to only include those that feature at least one of the UUIDs in the list. For simplcity, assume the whitelist file is a simple text file, where one UUID is listed per line. 

### Building the BLE Receiver Firmware

BLE Receivers are primarily responsible for, as the name implies, receiving BLE advertisements from other devices. The receivers should actively scan for advertisements, rather than passively, to ensure a lower latency. Only when the receiver encounters an advertisements featuring one of the whitelisted UUIDs, as presented by the controller, should it send a Serial message to the controller with information such as the receiver's ID, the advertiser's UUID, the RSSI, and a timestamp (defined by the controller, not receivers, for consistency). Furthermore, the BLE receiver should ensure a status indicator LED is kept on at all times. A "blink" sequence should also be defined fully in the firmware to blink the status indicator LED 3 times upon receiving a request from the controller for the receiver to blink its LED. Furthermore, a "lighting" command should be handle when received from the controller, where the lighting command specifies what user's lighting should be applied for that receiver, turning all lights off if an empty string is passed in for the target user.

You should also implement an alternative version of the firmware that uses ESP-NOW for communications. More precisely, the receivers in the system will exclusively use ESP-NOW for inbound and outbound communications, while one dedicated "gateway" node has a Serial connection to the central controller. If the ESP32 devices are all plugged in, an agentic workflow may be able to determine what MAC address the gateway is using, updating the receiver code to match this. If this is not possible, then the MAC address updating should be clearly deferred to the user.

### Building the Testing Suite

The central controller is the key component being tested with the testing suite, though the configuration UI can also be tested if desired. Skipping testing for the BLE receivers is permissible. Pytest is highly encouraged to be used to build unit tests for each submodule, including but not limited to RSSI averaging, Kalman filtering, user tracking, zone tracking, serial ingestion, etc.. Your tests should cover typical cases (e.g. a single user in the system meets the criteria to move forward to the next zone), unusual cases (e.g. the user enters the first zone / exits the last zone), and error cases (e.g. invalid Kalman Filter parameters provided, negative parameter selected for rolling window/average size).

### Running the Controller and Configuration Tool

Running the controller should be as simple as running Python with the entry point script. The entry point script can accept arguments via the CLI, or alternatively, can provide options in the form of a referenced JSON file. As mentioned in prior sections, the configuration tool can be started with the core controller, or it can be run separately via `npm run dev` if using a build tool such as Vite (particularly if using a framework such as Vue/React). It is highly recommended to set up the central controller with the `argparse` library to provide an accessible means for the user to retrieve information about the central controller's arguments.

### Testing the Central Controller and Configuration Tool

Industry standard tools are recommended for this purpose. For instance, pytest can be used to test all of the central controller's submodules and provide an intuitive output to the developer. For the configuration tool, the JavaScript functionality can be verified with a unit testing framework such as Jest. However, if using a framework such as React/Vue/etc., then directly using a unit testing framework such as Jest may prove to be difficult and can be left unimplemented for simplicity.

## Existing Starlight Implementation: Testing Procedure

NOTE: If you have built a new Starlight system, you can ignore the remainder of this document. Your testing setup may vary significantly from what follows.

## A Note on Hardware Utilized and These Instructions
The Starlight project's controller is developed with Python 3.14.3 on a 2025 Apple MacBook Air with 16 GB of memory and an Apple M4 processor. Earlier versions of Python 3 are likely to be compatible with the controller, though earlier versions admittedly have not yet been tested.

These instructions assume a bash/zsh interface commonly found in the "Terminal" app for Linux and MacOS devices. You should translate these instructions to Powershell or Command Prompt if it is determined that the device you are running on is a Windows device that is not within WSL. For instance, the `dir` command can be used on Windows in place of the `find` command.

We also assume the project's root directory is titled `starlight` and that it is placed within a user's home directory in their file system.

## Prerequisites
It is highly recommended to create a virtual environment with Python for testing purposes. This can be set up as follows:

### Step 1: Confirm Python is installed

Shell Command:
```
python3 --version
```

Expected Output (exact Python version may vary):
```
Python 3.14.3
```

As long as you do not encounter an error, you are okay to proceed. If an error appears, you should either 1) install Python and try again, or 2) stop now and request that the user install Python.

### Step 2: Confirm the venv module is installed

Shell Command: 
```
python3 -m venv -h
```

Expected Output:
```
usage: python3 -m venv [-h] [--system-site-packages] [--symlinks | --copies] [--clear] [--upgrade] [--without-pip] [--prompt PROMPT] [--upgrade-deps] [--without-scm-ignore-files] ENV_DIR [ENV_DIR ...]

Creates virtual Python environments in one or more target directories.

positional arguments:
...
```
As long as you get the help message, you are okay to proceed. If an error appears, such as the venv module not being installed, you should either 1) attempt to address the error, or 2) stop now and request that the user address the issue.

### Step 3: Determine where the project root directory is

Shell Command (assuming the project root directory name is kept as `starlight` after cloning the repository): 
```
find ~ -type d -name starlight 2>/dev/null
```

Expected Output (exact output may vary, but usually one entry is expected):
```
/Users/myuser/starlight
```

If exactly one path (e.g. in the form of /some/random/path) is found, proceed to step 4 now. If no entries are found, you should stop here and inform the user that a `starlight` project root directory could not be found in the user's home directory. If multiple entries are found, execute the following instruction with each entry identified to determine which directory to use in future steps

Shell Command (only execute this if more than one path was found from the last command):
```
find /Users/myuser/starlight -type f -name INSTRUCTIONS.md
```

Expected Output (exact path may vary, but the correct directory will return a non-empty output with an exit code of 0)
```
/Users/myuser/starlight/INSTRUCTIONS.md
```

### Step 4: Set the current working directory to the project's root directory

Using the identified path from the previous step, execute the following command

Shell Command:
```
cd /path/you/found
```

Expected Output *None*

If an error is returned, you should stop now and inform the user of the issue.

### Step 5: Create the virtual environment

Shell Command:
```
python3 -m venv starlight_venv
```

Expected Output: *None*

If the command executes successfully, you are okay to proceed. You can confirm this with the command `echo $?` that should produce an output of `0`. If an error appears or the exit code is non-zero, you should either 1) attempt to address the error, or 2) stop now and request that the user address the issue.

### Step 6: Activate the virtual environment

Shell Command: 
```
source starlight_venv/bin/activate
```

Expected Output: *None*

If the command fails to exeecute, you should either 1) attempt to address the error yourself, or 2) stop now and inform the user of the error encountered.

### Step 7: Install the python module dependencies

Shell Command: 
```
pip install -r requirements.txt
```

As long as the command executes successfully, you are okay to proceed. If an error appears, you should either 1) attempt to address the error yourself, or 2) stop now and inform the user of the error encountered.

## Evaluating the Central Controller

Assuming the current working directory is the root directory of the project (i.e. the same directory as this INSTRUCTIONS.md file), the Python-based central controller can be evaluted from a testing and linting perspective.

### Step 1: Run the Unit Tests

Shell Command:
```
pytest controller/tests
```

Expected Output: *Output varies, all tests should pass*

You must report if all tests pass, and if not, what number of tests have failed.

### Step 2: Run the linter

Shell Command:
```
pylint controller
```

Expected Output: *Output varies: the exit code may be non-zero, but pylint should not indicate that it failed to perform the linting process*

If the linting process produces no output, then report this. If not, you should report the score provided at the bottom of the output.

## Additional Notes to Present to the User
The developer acknowledges that testing for the ESP-32 microcontrollers that serve as BLE advertisement receivers is **not** supported at this time. When stating the results to the user, you should make this very clear to the user.
