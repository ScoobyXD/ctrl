using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

public sealed class CtrlChoiceButton : Button {
    public void TestClick() { OnClick(EventArgs.Empty); }
}

public sealed class CtrlExperimentChooser : Form {
    static readonly Color Background = Color.FromArgb(16, 20, 18), Card = Color.FromArgb(27, 33, 30), Ink = Color.FromArgb(235, 241, 233), Muted = Color.FromArgb(166, 182, 171), Lime = Color.FromArgb(209, 248, 119), Blue = Color.FromArgb(145, 207, 251);
    readonly string baselineRoot, hybridRoot;
    readonly Action<string> launch, openFolder;
    readonly Func<string, bool> available;
    readonly bool testing;
    readonly Label notice;
    readonly CtrlChoiceButton baselineLaunch, hybridLaunch, baselineFolder, hybridFolder;
    readonly System.Windows.Forms.Timer activationTimer;
    EventWaitHandle activationSignal;
    int launchRequests, folderRequests;

    public CtrlExperimentChooser(string baseline, string hybrid, Action<string> launcher, Action<string> folderOpener, Func<string, bool> readiness, bool test) {
        baselineRoot = Path.GetFullPath(baseline); hybridRoot = Path.GetFullPath(hybrid);
        launch = launcher; openFolder = folderOpener; available = readiness; testing = test;
        Text = "[CTRL] Choose a browser agent";
        ClientSize = new Size(1110, 720); MinimumSize = new Size(960, 710);
        StartPosition = FormStartPosition.CenterScreen; BackColor = Background; ForeColor = Ink;
        Font = new Font("Segoe UI", 10); AutoScaleMode = AutoScaleMode.Dpi;
        TableLayoutPanel main = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 5, Padding = new Padding(28) };
        main.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
        main.RowStyles.Add(new RowStyle(SizeType.Absolute, 65));
        main.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        main.RowStyles.Add(new RowStyle(SizeType.Absolute, 68));
        main.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        Controls.Add(main);
        Label title = Label("[CTRL]  /  CHOOSE YOUR MODE", Lime); title.Font = new Font("Segoe UI", 21, FontStyle.Bold);
        main.Controls.Add(title, 0, 0);
        main.Controls.Add(Label("Two separate local applications for your normal Chrome.\r\nChoose a mode to open its native debugger, then enter a task and press Run there.", Muted), 0, 1);
        TableLayoutPanel cards = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1 };
        cards.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); cards.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        cards.Controls.Add(ModeCard(false, out baselineLaunch, out baselineFolder), 0, 0);
        cards.Controls.Add(ModeCard(true, out hybridLaunch, out hybridFolder), 1, 0);
        main.Controls.Add(cards, 0, 2);
        Label ownership = Label("ONE CONTROLLER AT A TIME\r\nStop the current app before connecting Chrome from the other mode. This chooser never stops an app, changes a task, or reads credentials.", Color.FromArgb(244, 201, 142));
        ownership.Padding = new Padding(4, 8, 4, 0); main.Controls.Add(ownership, 0, 3);
        notice = Label("Ready for your selection. Local file availability is shown; no connection has been checked.", Muted);
        main.Controls.Add(notice, 0, 4);
        baselineLaunch.Click += delegate { Launch(baselineRoot, "Baseline"); };
        hybridLaunch.Click += delegate { Launch(hybridRoot, "Hybrid experiment"); };
        baselineFolder.Click += delegate { Open(baselineRoot); };
        hybridFolder.Click += delegate { Open(hybridRoot); };
        activationTimer = new System.Windows.Forms.Timer { Interval = 200 };
        activationTimer.Tick += delegate { RestoreRequestedWindow(); };
    }

    public void WatchActivation(EventWaitHandle signal) { activationSignal = signal; activationTimer.Start(); }
    void RestoreRequestedWindow() {
        if (activationSignal == null || !activationSignal.WaitOne(0) || IsDisposed) return;
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        if (!Visible) Show();
        if (!testing) { BringToFront(); Activate(); }
    }
    protected override void Dispose(bool disposing) {
        if (disposing) { activationTimer.Stop(); activationTimer.Dispose(); }
        base.Dispose(disposing);
    }
    static Label Label(string value, Color color) { return new Label { Text = value, Dock = DockStyle.Fill, ForeColor = color, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true }; }
    static CtrlChoiceButton Button(string value, Color color) { return new CtrlChoiceButton { Text = value, Dock = DockStyle.Fill, FlatStyle = FlatStyle.Flat, BackColor = color, ForeColor = Background, Font = new Font("Segoe UI", 11, FontStyle.Bold), Margin = new Padding(0, 4, 0, 4) }; }
    Control ModeCard(bool hybrid, out CtrlChoiceButton start, out CtrlChoiceButton folder) {
        string root = hybrid ? hybridRoot : baselineRoot;
        Color accent = hybrid ? Blue : Lime;
        TableLayoutPanel card = new TableLayoutPanel { Dock = DockStyle.Fill, BackColor = Card, Padding = new Padding(20), Margin = new Padding(hybrid ? 8 : 0, 0, hybrid ? 0 : 8, 0), ColumnCount = 1, RowCount = 8 };
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 24));
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
        card.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 51));
        card.RowStyles.Add(new RowStyle(SizeType.Absolute, 36));
        Label tag = Label(hybrid ? "EXPERIMENTAL  /  SEPARATE INSTALL" : "BASELINE  /  EXISTING INSTALL", accent); tag.Font = new Font("Segoe UI", 9, FontStyle.Bold);
        card.Controls.Add(tag, 0, 0);
        Label heading = Label(hybrid ? "Gemini planner + Jev" : "Ultra-fast / Jev only", Ink); heading.Font = new Font("Segoe UI", 19, FontStyle.Bold);
        card.Controls.Add(heading, 0, 1);
        card.Controls.Add(Label(hybrid
            ? "TipTour Windows browser adaptation.\r\nGemini proposes browser subgoals; Jev chooses observed actions. Public planner summaries and Jev probabilities appear in the native debugger."
            : "The original local CTRL workflow.\r\nJev chooses from observed browser actions. Inspect offered options, probabilities, validation, and the recorded action timeline.", Muted), 0, 2);
        card.Controls.Add(Label(hybrid ? "Local port 8767   |   Emergency stop: Ctrl+Alt+Shift+H" : "Local port 8766   |   Emergency stop: Ctrl+Alt+Shift+S", accent), 0, 3);
        Label path = Label(root, Muted); path.Font = new Font("Consolas", 8); card.Controls.Add(path, 0, 4);
        bool ready = available(root);
        card.Controls.Add(Label(ready ? "Launcher found locally. Runtime status is not checked." : "Launcher missing. Restore this install before launching.", ready ? Muted : Color.FromArgb(254, 163, 151)), 0, 5);
        start = Button(hybrid ? "Open Hybrid experiment" : "Open Ultra-fast", accent); start.Enabled = ready;
        card.Controls.Add(start, 0, 6);
        folder = Button("Open source folder", Card); folder.ForeColor = Muted; folder.Font = new Font("Segoe UI", 9); folder.FlatAppearance.BorderColor = Color.FromArgb(63, 79, 69); card.Controls.Add(folder, 0, 7);
        return card;
    }
    void Launch(string root, string mode) {
        if (!available(root)) { notice.Text = mode + " launcher is unavailable. No app was started."; return; }
        try { launch(root); launchRequests++; notice.Text = mode + " launch requested. No browser task was submitted. Stop the other app before connecting Chrome."; }
        catch (Exception error) { notice.Text = "Launch failed: " + error.Message; }
    }
    void Open(string root) {
        try { openFolder(root); folderRequests++; notice.Text = "Opened source folder: " + root; }
        catch (Exception error) { notice.Text = "Folder could not open: " + error.Message; }
    }
    protected override bool ShowWithoutActivation { get { return testing; } }
    public void Verify(string preview, List<string> launches, List<string> folders) {
        if (launches.Count != 0 || folders.Count != 0) throw new InvalidOperationException("Chooser performed an action before selection.");
        ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; Location = new Point(-32000, -32000); Show(); Application.DoEvents();
        string testSignalName = "Local\\CTRL-Chooser-Test-" + Guid.NewGuid().ToString("N");
        using (EventWaitHandle receiver = new EventWaitHandle(false, EventResetMode.AutoReset, testSignalName)) {
            using (EventWaitHandle sender = EventWaitHandle.OpenExisting(testSignalName)) {
                WatchActivation(receiver); WindowState = FormWindowState.Minimized;
                RestoreRequestedWindow();
                if (WindowState != FormWindowState.Minimized) throw new InvalidOperationException("Chooser restored without a launch request.");
                sender.Set(); RestoreRequestedWindow();
                if (WindowState != FormWindowState.Normal || !Visible || launches.Count != 0 || folders.Count != 0) throw new InvalidOperationException("Repeated launch did not restore only the existing chooser.");
                WindowState = FormWindowState.Minimized; RestoreRequestedWindow();
                if (WindowState != FormWindowState.Minimized) throw new InvalidOperationException("A consumed launch request repeated.");
                WindowState = FormWindowState.Normal;
            }
            activationTimer.Stop(); activationSignal = null;
        }
        baselineLaunch.TestClick(); hybridLaunch.TestClick(); baselineFolder.TestClick(); hybridFolder.TestClick();
        if (launchRequests != 2 || folderRequests != 2 || launches.Count != 2 || folders.Count != 2 || launches[0] != baselineRoot || launches[1] != hybridRoot || folders[0] != baselineRoot || folders[1] != hybridRoot) throw new InvalidOperationException("Mode choices did not route independently to their own roots.");
        notice.Text = "Synthetic self-test: both launch buttons and both folder buttons routed to test stubs only.";
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(preview)));
        Size regular = ClientSize; ClientSize = new Size(944, 671); PerformLayout(); Application.DoEvents();
        foreach (Control button in new Control[] { baselineLaunch, hybridLaunch, baselineFolder, hybridFolder }) {
            Rectangle bounds = RectangleToClient(button.RectangleToScreen(button.ClientRectangle));
            if (bounds.Right > ClientSize.Width || bounds.Left < 0 || bounds.Bottom > ClientSize.Height || bounds.Height < 20) throw new InvalidOperationException("A chooser command is clipped at minimum size.");
        }
        string small = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(preview)), Path.GetFileNameWithoutExtension(preview) + ".minimum.png");
        using (Bitmap bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(small, ImageFormat.Png); }
        ClientSize = regular; PerformLayout(); Application.DoEvents();
        using (Bitmap bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(preview, ImageFormat.Png); }
        Hide();
    }
}
public static class CtrlExperimentChooserApp {
    public static void Run(string baselineRoot, string hybridRoot) {
        Application.EnableVisualStyles();
        string hash;
        using (SHA256 sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(baselineRoot).ToLowerInvariant()))).Replace("-", "").Substring(0, 16);
        bool created;
        using (EventWaitHandle showRequest = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\CTRL-ExperimentChooser-Show-" + hash))
        using (Mutex mutex = new Mutex(true, "Local\\CTRL-ExperimentChooser-" + hash, out created)) {
            if (!created) { showRequest.Set(); return; }
            try {
                using (CtrlExperimentChooser form = new CtrlExperimentChooser(baselineRoot, hybridRoot,
                    delegate(string root) {
                        string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
                        Process.Start(new ProcessStartInfo(powershell, "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(root, "Start-CTRL.ps1") + "\"") { UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, WorkingDirectory = root });
                    },
                    delegate(string root) { Process.Start(new ProcessStartInfo("explorer.exe", "\"" + root + "\"") { UseShellExecute = true }); },
                    delegate(string root) { return File.Exists(Path.Combine(root, "Start-CTRL.ps1")); }, false)) {
                    form.WatchActivation(showRequest); Application.Run(form);
                }
            } finally { mutex.ReleaseMutex(); }
        }
    }
    public static string Verify(string preview) {
        Application.EnableVisualStyles();
        List<string> launches = new List<string>(), folders = new List<string>();
        using (CtrlExperimentChooser form = new CtrlExperimentChooser(@"C:\Synthetic\ctrl", @"C:\Synthetic\hybrid", delegate(string root) { launches.Add(root); }, delegate(string root) { folders.Add(root); }, delegate(string root) { return true; }, true)) form.Verify(preview, launches, folders);
        using (CtrlExperimentChooser missing = new CtrlExperimentChooser(@"C:\Synthetic\missing", @"C:\Synthetic\missing-hybrid", delegate(string root) { throw new InvalidOperationException("Missing launcher started."); }, delegate(string root) { }, delegate(string root) { return false; }, true)) { }
        return "{\"ok\":true,\"synthetic_only\":true,\"no_auto_launch\":true,\"repeat_launch_restores\":true,\"distinct_roots\":true,\"launch_stub_calls\":" + launches.Count + ",\"folder_stub_calls\":" + folders.Count + ",\"minimum_layout\":true}";
    }
}
