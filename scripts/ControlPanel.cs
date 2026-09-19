using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public sealed class CtrlPanelButton : Button {
    public void TestClick() { OnClick(EventArgs.Empty); }
}

public sealed class CtrlPanelForm : Form {
    const int HotkeyId = 8176;
    [DllImport("user32.dll")] static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint key);
    [DllImport("user32.dll")] static extern bool UnregisterHotKey(IntPtr window, int id);
    readonly string root;
    readonly Func<string, string[], Process> launch;
    readonly bool testing;
    readonly HttpClient http;
    readonly System.Windows.Forms.Timer timer;
    readonly Label status;
    readonly Label counts;
    readonly Label shortcut;
    readonly CtrlPanelButton stop;
    readonly CtrlPanelButton pause;
    readonly CtrlPanelButton dashboard;
    readonly CtrlPanelButton restart;
    string token;
    bool polling, online, turningOff, closing, stopUnconfirmed;
    DateTime stopRequestedAt;
    Process stopProcess, startProcess;
    public bool HotkeyRegistered { get; private set; }

    public CtrlPanelForm(string directory, Func<string, string[], Process> launcher, bool test) {
        root = directory; launch = launcher; testing = test;
        Text = "[CTRL] Control Panel";
        ClientSize = new Size(382, 355);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false; MinimizeBox = false; TopMost = true;
        AutoScaleMode = AutoScaleMode.Dpi;
        BackColor = Color.FromArgb(24, 27, 22);
        ForeColor = Color.FromArgb(238, 241, 231);
        Font = new Font("Segoe UI", 10);
        StartPosition = FormStartPosition.Manual;
        Rectangle area = Screen.PrimaryScreen.WorkingArea;
        Location = new Point(area.Right - 408, area.Top + 35);
        Label heading = AddLabel("[CTRL]  /  AGENT CONTROL", 19, 15, 345, 25);
        heading.Font = new Font("Segoe UI", 11, FontStyle.Bold);
        stop = AddButton("STOP AGENT / TURN OFF", 18, 48, 346, 66);
        stop.BackColor = Color.FromArgb(173, 43, 35);
        stop.Font = new Font("Segoe UI", 12, FontStyle.Bold);
        stop.AccessibleDescription = "Turn off the local agent server. Chrome stays open.";
        shortcut = AddLabel("Emergency shortcut: Ctrl + Alt + Shift + S", 21, 121, 342, 24);
        shortcut.ForeColor = Color.FromArgb(224, 199, 155);
        status = AddLabel("Checking local agent...", 21, 154, 342, 39);
        counts = AddLabel("Actions: --     Host: --", 21, 198, 342, 24);
        pause = AddButton("Pause", 18, 232, 108, 39);
        dashboard = AddButton("Dashboard", 135, 232, 111, 39);
        restart = AddButton("Restart app", 255, 232, 109, 39);
        pause.Enabled = dashboard.Enabled = restart.Enabled = false;
        Label notice = AddLabel("Stop leaves this panel open. Restart is always manual.", 21, 284, 342, 31);
        notice.Font = new Font("Segoe UI", 8); notice.ForeColor = Color.FromArgb(167, 183, 151);
        Label closeNotice = AddLabel("Closing this panel also turns the agent off.", 21, 321, 342, 21);
        closeNotice.Font = new Font("Segoe UI", 8);
        stop.Click += delegate { StopAgent(); };
        pause.Click += async delegate { await PauseAgent(); };
        dashboard.Click += delegate { OpenDashboard(); };
        restart.Click += delegate { RestartAgent(); };
        HttpClientHandler handler = new HttpClientHandler(); handler.UseProxy = false;
        http = new HttpClient(handler); http.Timeout = TimeSpan.FromMilliseconds(1200);
        timer = new System.Windows.Forms.Timer(); timer.Interval = 500;
        timer.Tick += async delegate { await Poll(); };
        Shown += async delegate {
            if (!HotkeyRegistered) shortcut.Text = "Shortcut unavailable. The Stop button still works.";
            if (!testing) { timer.Start(); await Poll(); }
        };
        FormClosing += delegate { if (!testing && !closing) { closing = true; StopAgent(); } };
    }

    Label AddLabel(string text, int x, int y, int width, int height) {
        Label label = new Label(); label.Text = text; label.Location = new Point(x, y); label.Size = new Size(width, height);
        Controls.Add(label); return label;
    }
    CtrlPanelButton AddButton(string text, int x, int y, int width, int height) {
        CtrlPanelButton button = new CtrlPanelButton(); button.Text = text;
        button.Location = new Point(x, y); button.Size = new Size(width, height);
        button.FlatStyle = FlatStyle.Flat; button.BackColor = Color.FromArgb(45, 54, 36);
        Controls.Add(button); return button;
    }
    static bool Alive(Process process) { try { return process != null && !process.HasExited; } catch { return false; } }
    void StopAgent() {
        if (Alive(stopProcess)) return;
        turningOff = true; status.Text = "Turning the agent off...";
        stopUnconfirmed = false; stopRequestedAt = DateTime.UtcNow;
        pause.Enabled = restart.Enabled = dashboard.Enabled = false;
        try { stopProcess = launch("Stop-CTRL.ps1", new string[] { "-Force" }); }
        catch { status.Text = "Could not launch Stop. Run Stop-CTRL.ps1 directly."; }
    }
    void RestartAgent() {
        if (online || Alive(startProcess) || Alive(stopProcess)) return;
        restart.Enabled = false; turningOff = false; status.Text = "Starting the app at your request...";
        try {
            string generationPath = Path.Combine(root, ".ctrl", "stop-generation");
            string generation = testing || !File.Exists(generationPath) ? "initial" : File.ReadAllText(generationPath).Trim();
            if (generation != "initial" && !Regex.IsMatch(generation, "^[a-f0-9]{32}$")) throw new InvalidOperationException();
            startProcess = launch("Start-CTRL.ps1", new string[] { "-NoBrowser", "-NoPanel", "-StopGeneration", generation });
        }
        catch { status.Text = "Could not start the app. Use Start-CTRL.ps1."; }
    }
    void OpenDashboard() {
        if (!online) return;
        try { launch("scripts\\Debug-CTRL.ps1", new string[0]); }
        catch { status.Text = "Could not open the debugger. Use Start-CTRL.ps1."; }
    }
    async Task PauseAgent() {
        if (String.IsNullOrEmpty(token)) return;
        pause.Enabled = false;
        try {
            using (HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, "http://127.0.0.1:8766/api/pause")) {
                request.Headers.Add("X-CTRL-Token", token); request.Content = new StringContent("{}", Encoding.UTF8, "application/json");
                using (HttpResponseMessage response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead)) { }
            }
        } catch { if (!IsDisposed) status.Text = "Pause unavailable. Stop turns the agent off."; }
    }
    void Offline() {
        online = false; token = null; pause.Enabled = dashboard.Enabled = false;
        bool recordedServer = !testing && File.Exists(Path.Combine(root, ".ctrl", "server.pid"));
        restart.Enabled = !Alive(startProcess) && !Alive(stopProcess) && !recordedServer;
        counts.Text = "Actions: --     Host: --";
        if (Alive(startProcess)) status.Text = "Starting the app at your request...";
        else if (Alive(stopProcess)) status.Text = "Turning the agent off...";
        else { status.Text = recordedServer ? "Agent is unreachable. Press STOP to turn it off." : "Agent is off. Restart app to turn it on."; turningOff = false; }
    }
    async Task Poll() {
        if (polling || closing || testing) return;
        polling = true;
        try {
            if (String.IsNullOrEmpty(token)) {
                string html = await http.GetStringAsync("http://127.0.0.1:8766/");
                token = Regex.Match(html, "name=\"ctrl-token\" content=\"([^\"]+)\"").Groups[1].Value;
                if (String.IsNullOrEmpty(token)) throw new InvalidOperationException();
            }
            Dictionary<string, object> data;
            using (HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Get, "http://127.0.0.1:8766/api/control")) {
                request.Headers.Add("X-CTRL-Token", token);
                using (HttpResponseMessage response = await http.SendAsync(request)) {
                    response.EnsureSuccessStatusCode();
                    data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(await response.Content.ReadAsStringAsync());
                }
            }
            if (IsDisposed || closing) return;
            online = true; restart.Enabled = false; dashboard.Enabled = !turningOff;
            if (turningOff && !Alive(stopProcess) && DateTime.UtcNow.Subtract(stopRequestedAt).TotalSeconds > 2) { turningOff = false; stopUnconfirmed = true; }
            bool running = data.ContainsKey("running") && Convert.ToBoolean(data["running"]);
            bool busy = data.ContainsKey("busy") && Convert.ToBoolean(data["busy"]);
            pause.Enabled = (running || busy) && !turningOff;
            string mode = data.ContainsKey("status") ? Convert.ToString(data["status"]).Replace('_', ' ') : "Ready";
            if (running) mode = "RUNNING"; else if (busy) mode = "Working";
            status.Text = turningOff ? "Turning the agent off..." : stopUnconfirmed ? "Agent is still online. Press STOP again." : "Agent: " + mode;
            string currentHost = data.ContainsKey("current_host") ? Convert.ToString(data["current_host"]) : "--";
            int actionCount = data.ContainsKey("action_count") ? Convert.ToInt32(data["action_count"]) : 0;
            counts.Text = "Actions: " + actionCount + "     Host: " + (String.IsNullOrEmpty(currentHost) ? "--" : currentHost);
        } catch { if (!IsDisposed && !closing) Offline(); }
        finally { polling = false; }
    }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e); HotkeyRegistered = RegisterHotKey(Handle, HotkeyId, 0x4007, 0x53);
    }
    protected override bool ShowWithoutActivation { get { return testing; } }
    protected override void WndProc(ref Message message) {
        if (message.Msg == 0x0312 && message.WParam.ToInt32() == HotkeyId) StopAgent();
        base.WndProc(ref message);
    }
    protected override void OnHandleDestroyed(EventArgs e) {
        if (HotkeyRegistered) { UnregisterHotKey(Handle, HotkeyId); HotkeyRegistered = false; }
        base.OnHandleDestroyed(e);
    }
    protected override void Dispose(bool disposing) {
        if (disposing) { timer.Stop(); timer.Dispose(); http.Dispose(); token = null; }
        base.Dispose(disposing);
    }
    public void VerifyControls(string preview, List<string> requests) {
        ShowInTaskbar = false; Location = new Point(-32000, -32000); Show(); Application.DoEvents();
        IntPtr handle = Handle;
        if (!TopMost || !HotkeyRegistered || Controls.Count != 10) throw new InvalidOperationException("Native controls or global shortcut failed verification.");
        stop.TestClick();
        if (requests.Count != 1 || requests[0] != "Stop-CTRL.ps1 -Force") throw new InvalidOperationException("Stop button did not route to the verified stop script.");
        Message key = Message.Create(handle, 0x0312, new IntPtr(HotkeyId), IntPtr.Zero); WndProc(ref key);
        if (requests.Count != 2 || requests[1] != "Stop-CTRL.ps1 -Force") throw new InvalidOperationException("Global shortcut did not route to Stop.");
        Offline();
        if (online || requests.Count != 2) throw new InvalidOperationException("Stopping did not preserve the off state.");
        // Self-test never inspects or changes a real server's lifecycle.
        restart.Enabled = true;
        restart.TestClick();
        if (requests.Count != 3 || requests[2] != "Start-CTRL.ps1 -NoBrowser -NoPanel -StopGeneration initial") throw new InvalidOperationException("Manual restart routing failed.");
        Offline(); PerformLayout();
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(preview)));
        using (Bitmap bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(preview, ImageFormat.Png); }
        Hide();
    }
}

public static class CtrlPanelApp {
    public static ProcessStartInfo LaunchInfo(string root, string script, string[] arguments) {
        string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
        string command = "-NoProfile -ExecutionPolicy Bypass -STA -File \"" + Path.Combine(root, script) + "\" " + String.Join(" ", arguments);
        bool interactive = String.Equals(script.Replace('/', '\\'), "scripts\\Debug-CTRL.ps1", StringComparison.OrdinalIgnoreCase);
        // CREATE_NO_WINDOW hides PowerShell's console; SW_HIDE would also hide the
        // first WinForms window. Background lifecycle helpers keep their old style.
        return new ProcessStartInfo(powershell, command) { UseShellExecute = false, CreateNoWindow = true, WindowStyle = interactive ? ProcessWindowStyle.Normal : ProcessWindowStyle.Hidden, WorkingDirectory = root };
    }
    static Process Launch(string root, string script, string[] arguments) {
        return Process.Start(LaunchInfo(root, script, arguments));
    }
    public static void Run(string root) {
        Application.EnableVisualStyles();
        string hash;
        using (SHA256 sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToLowerInvariant()))).Replace("-", "").Substring(0, 16);
        bool created;
        using (Mutex mutex = new Mutex(true, "Local\\CTRL-Panel-" + hash, out created)) {
            if (!created) return;
            string pidFile = Path.Combine(root, ".ctrl", "panel.pid");
            Directory.CreateDirectory(Path.GetDirectoryName(pidFile));
            string processId = Process.GetCurrentProcess().Id.ToString(); File.WriteAllText(pidFile, processId);
            try { using (CtrlPanelForm form = new CtrlPanelForm(root, (script, args) => Launch(root, script, args), false)) Application.Run(form); }
            finally { if (File.Exists(pidFile) && File.ReadAllText(pidFile).Trim() == processId) File.Delete(pidFile); mutex.ReleaseMutex(); }
        }
    }
    public static string Verify(string root, string preview) {
        Application.EnableVisualStyles();
        List<string> requests = new List<string>();
        using (CtrlPanelForm form = new CtrlPanelForm(root, (script, args) => { requests.Add(script + " " + String.Join(" ", args)); return null; }, true)) {
            form.VerifyControls(preview, requests);
            return new JavaScriptSerializer().Serialize(new { ok = true, topmost = form.TopMost, hotkey_registered = form.HotkeyRegistered, stop_click_routed = true, shortcut_routed = true, restart_manual = true, preview_path = Path.GetFullPath(preview) });
        }
    }
}
