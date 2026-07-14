# Antigravity PCB Preview - Setup & User Guide

Welcome to the **PCB Preview** project! This guide is written for everyone—even if you don't have a deep technical background. It will walk you through getting the software onto your computer, running it, and understanding what you can do with it.

---

## 🚀 Part 1: Getting Started (Setup)

Before you begin, you need a few standard tools installed on your computer:
1. **Git** (to download the code)
2. **Python** (version 3.10 or newer, to run the code)
3. **Poetry** (a tool that installs the necessary Python packages)

### Step 1: Download the Code
Open your terminal (Command Prompt or PowerShell on Windows, Terminal on Mac) and run this command to download the code to your computer:
```bash
git clone -b friend-testing https://github.com/Abhi378-2005/PCB_Preview.git
```
*(This command automatically downloads the `friend-testing` branch, which contains the most up-to-date features).*

### Step 2: Open the Folder
Move inside the folder you just downloaded:
```bash
cd PCB_Preview
```

### Step 3: Install the Dependencies
We use a tool called Poetry to manage all the background software our app needs. Install them by running:
```bash
poetry install
```

### Step 4: Run the Application
Start the application with this simple command:
```bash
poetry run pcb-preview
```
Alternatively, you can run:
```bash
python main.py
```
**That's it!** The server will start running, and your web browser should automatically open a new tab pointing to `http://localhost:5050`.

---

## 🎨 Part 2: Understanding the Features

Once the web page opens, you'll see the main interface of our application. Here is what it does and how to use it:

### 1. Drag & Drop PCB Viewing (Gerber Files)
Printed Circuit Boards (PCBs) are designed using files called "Gerbers" (files ending in `.gbr`). 
- Simply drag and drop your `.gbr` files onto the web page.
- The app will automatically read them and draw a highly accurate, zoomable preview of your circuit board.
- It supports multiple layers (like Copper, Solder Mask, Silkscreen) and will color-code them so you can see exactly how they stack up.

### 2. G-Code Generation (Making it printable)
Once your board looks good, the app can convert that picture into **G-Code**—the language that CNC machines and Laser Engravers understand.
- **Trace Mode:** The laser will smoothly follow the exact lines of your copper traces (best for drawing).
- **Raster Mode:** The laser will scan back and forth horizontally, burning away everything *except* the copper (best for making etch-resist stencils).

### 3. USB CNC Connection
You don't need a separate program to send the G-Code to your machine!
- Connect your CNC machine or Laser Engraver via USB.
- Use the **Connect** panel on the webpage to link directly to your machine.
- You can now talk directly to the machine from your browser.

### 4. Machine Controls (Jogging & Homing)
- **Homing:** Click the "Home" button to tell the machine to find its starting `(0,0)` position.
- **Jogging:** Use the on-screen arrow buttons to manually nudge the laser head up, down, left, or right to get it perfectly aligned over your material.

### 5. Live CNC Synchronization (The "Magic" Feature)
When you click **Stream to CNC**, the app starts sending the G-Code to your machine.
- As the physical machine moves in real life, a **live crosshair** will move across your PCB preview on the screen!
- It draws a magenta trail showing you *exactly* what the machine has burned so far.
- This lets you monitor the physical job perfectly in real-time right from your browser.
