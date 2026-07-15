# Antigravity PCB Preview - Detailed Setup & User Guide

Welcome to the **PCB Preview** project! This guide is written for everyone, especially those who do not have a technical programming background. It will walk you through every single step of getting the software onto your computer, running it, and understanding exactly what each button does.

---

## 🚀 Part 1: Getting Started (Initial Setup)

Before you can download and run this app, you need three basic tools installed on your computer. If you don't have them, here is how to get them:

### 1. Install Git
Git is a tool used to download code from GitHub.
- **Windows / Mac:** Go to [git-scm.com/downloads](https://git-scm.com/downloads) and download the installer for your computer. Run the installer and just keep clicking "Next" to accept all the default settings.

### 2. Install Python
Python is the programming language this app is built with. You need **version 3.10 or newer**.
- Go to [python.org/downloads](https://www.python.org/downloads/) and download the latest version.
- **CRITICAL STEP (Windows users):** When you open the installer, look at the very bottom of the first screen. You **must** check the box that says **"Add Python to PATH"** before clicking Install. If you miss this, the app won't run!

### 3. Install Poetry
Poetry is a helper tool that automatically downloads all the extra background software our app needs.
- Open your terminal. 
  - *On Windows:* Press the `Windows Key`, type `powershell`, and hit Enter.
  - *On Mac:* Press `Cmd + Space`, type `Terminal`, and hit Enter.
- Copy and paste this exact command into the terminal and hit Enter:
```bash
curl -sSL https://install.python-poetry.org | python3 -
```
*(If the command fails on Windows, try replacing `python3` with `python`).*

---

## 📥 Part 2: Downloading the Code

Now that your computer has the right tools, let's download the PCB Preview app.

### Step 1: Clone the Repository
In your terminal, navigate to the folder where you want to save the app (for example, your Desktop). Then, run this command to download the code:
```bash
git clone -b friend-testing https://github.com/Abhi378-2005/PCB_Preview.git
```
*(This command creates a new folder called `PCB_Preview` and downloads the most up-to-date `friend-testing` branch).*

### Step 2: Open the Folder
Tell your terminal to look inside the folder you just downloaded by running:
```bash
cd PCB_Preview
```

### Step 3: Install Dependencies
Now we tell Poetry to install everything the app needs to run. Run:
```bash
poetry install
```
*(This might take a minute or two as it downloads files from the internet. You will see a bunch of text scrolling by—this is normal!)*

---

## 🏃 Part 3: Running the Application

You are now ready to start the app!

### Start the Server
Make sure you are still inside the `PCB_Preview` folder in your terminal, and run:
```bash
poetry run pcb-preview
```
*(If that doesn't work, you can also try running `python main.py`).*

**What happens next?**
1. Your terminal will show some text saying the "Uvicorn" server has started.
2. After about 1 second, your default web browser (like Chrome or Edge) will automatically open a new tab pointing to `http://localhost:5050`. 
3. **Important:** Do not close the terminal window while you are using the app! The terminal is the "engine" running in the background. When you are done using the app, you can close the terminal to shut it down.

---

## 🎨 Part 4: Using the Application Features

Once the web page opens, you'll see the main interface. Here is exactly how to use it:

### 1. Drag & Drop PCB Viewing (Gerber Files)
Printed Circuit Boards (PCBs) are designed using files called "Gerbers" (files ending in `.gbr`). 
- Locate your `.gbr` files on your computer.
- Drag and drop them directly onto the large preview area on the web page.
- The app will automatically read them and draw a highly accurate, color-coded picture of your circuit board. You can scroll to zoom in and out.

### 2. G-Code Generation (Making it printable)
Your CNC machine or Laser Engraver doesn't understand pictures; it only understands instructions called **G-Code**. The panel on the left will convert your Gerber picture into G-Code.
- **Trace Mode:** The laser will smoothly outline the exact paths of your copper traces. This is best if you just want to draw the circuit board.
- **Raster Mode:** The laser will scan back and forth horizontally like a regular paper printer, burning away everything *except* the copper. This is used for creating etch-resist stencils.
- Click **"Generate G-Code"** when you are ready.

### 3. USB CNC Connection
You don't need a separate program to send the G-Code to your machine!
- Plug your CNC machine or Laser Engraver into your computer via USB.
- On the webpage, look for the **Connect** panel.
- Select your machine's **COM port** from the dropdown (e.g., `COM3` on Windows or `/dev/ttyUSB0` on Mac). 
  - *Tip: If you see multiple ports and aren't sure which one is your CNC machine, simply unplug the USB cable, click the refresh icon to see which port disappeared, then plug it back in and select that one. It often shows up as something like `USB-SERIAL CH340` or `Arduino`.*
- Ensure the Baud Rate is correct (usually `115200` for GRBL machines).
- Click the **Connect** button. 

### 4. Machine Controls (Jogging & Homing)
Once connected, you can manually move the machine using the web interface:
- **Homing ($H):** Click the "Home" button to tell the machine to automatically move to its starting `(0,0)` position. It does this by moving until it hits its physical limit switches.
- **Jogging:** Use the on-screen arrow buttons to manually nudge the laser head up, down, left, or right to get it perfectly aligned over your raw material. You can change the "Step Size" to move it in larger or smaller increments (like 1mm vs 10mm).

### 5. Live CNC Synchronization (The "Magic" Feature)
When you are ready to start burning your PCB:
- Click the **Stream to CNC** button. The app will start sending the G-Code line-by-line to your machine.
- As the physical machine moves in real life, a **live crosshair** will move across your PCB preview on the screen!
- It draws a magenta trail showing you *exactly* what the machine has burned so far. This lets you sit back and monitor the physical job perfectly in real-time right from your browser window.
