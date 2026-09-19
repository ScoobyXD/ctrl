using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Globalization;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public static class CtrlDebugJson {
    public static readonly JavaScriptSerializer Serializer = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
    public static Dictionary<string, object> Map(object value) { return value as Dictionary<string, object> ?? new Dictionary<string, object>(); }
    public static object Get(object value, string name) { object found; return Map(value).TryGetValue(name, out found) ? found : null; }
    public static string Text(object value, string name) { object found = Get(value, name); return found == null ? "" : Convert.ToString(found, CultureInfo.InvariantCulture); }
    public static bool Flag(object value, string name) { return Object.Equals(Get(value, name), true); }
    public static List<object> Items(object value) { List<object> result = new List<object>(); IEnumerable items = value as IEnumerable; if (items != null && !(value is string)) foreach (object item in items) result.Add(item); return result; }
    public static string Probability(object value) {
        double number;
        if (value == null || !Double.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), NumberStyles.Float, CultureInfo.InvariantCulture, out number) || Double.IsNaN(number) || Double.IsInfinity(number) || number < 0 || number > 1) return "\u2014";
        return (number * 100).ToString("0.0", CultureInfo.InvariantCulture) + "%";
    }
    public static string Allowed(object value) { return Object.Equals(value, true) ? "Allowed" : Object.Equals(value, false) ? "Rejected" : "Not evaluated"; }
    public static string Dump(object value) { return Serializer.Serialize(value); }
    public static Dictionary<string, object> Parse(string value) { return Map(Serializer.DeserializeObject(value)); }
}

public interface ICtrlDebugApi {
    Task<Dictionary<string, object>> Get(string path);
    Task<Dictionary<string, object>> Post(string path, Dictionary<string, object> body);
}
public sealed class CtrlDebugApi : ICtrlDebugApi, IDisposable {
    readonly HttpClient http;
    string token;
    public CtrlDebugApi() { HttpClientHandler handler = new HttpClientHandler { UseProxy = false }; http = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(8) }; }
    async Task<Dictionary<string, object>> Send(string path, Dictionary<string, object> body) {
        if (String.IsNullOrEmpty(token)) {
            string html = await http.GetStringAsync("http://127.0.0.1:8766/");
            token = Regex.Match(html, "name=\"ctrl-token\" content=\"([^\"]+)\"").Groups[1].Value;
            if (String.IsNullOrEmpty(token)) throw new InvalidOperationException("The local CTRL session is unavailable.");
        }
        using (HttpRequestMessage request = new HttpRequestMessage(body == null ? HttpMethod.Get : HttpMethod.Post, "http://127.0.0.1:8766" + path)) {
            request.Headers.Add("X-CTRL-Token", token);
            if (body != null) request.Content = new StringContent(CtrlDebugJson.Dump(body), Encoding.UTF8, "application/json");
            using (HttpResponseMessage response = await http.SendAsync(request)) {
                string content = await response.Content.ReadAsStringAsync();
                if (!response.IsSuccessStatusCode) {
                    if ((int)response.StatusCode == 401 || (int)response.StatusCode == 403) token = null;
                    string message = "";
                    try { message = CtrlDebugJson.Text(CtrlDebugJson.Parse(content), "error"); } catch { }
                    throw new InvalidOperationException(String.IsNullOrEmpty(message) ? "The local request failed. No action was retried." : message);
                }
                return CtrlDebugJson.Parse(content);
            }
        }
    }
    public Task<Dictionary<string, object>> Get(string path) { return Send(path, null); }
    public Task<Dictionary<string, object>> Post(string path, Dictionary<string, object> body) { return Send(path, body); }
    public void Dispose() { token = null; http.Dispose(); }
}
public sealed class CtrlDebugTab {
    public string Id, Label;
    public override string ToString() { return Label; }
}
public sealed class CtrlDebugButton : Button { public void TestClick() { OnClick(EventArgs.Empty); } }

public sealed class CtrlDebugForm : Form {
    static readonly Color Background = Color.FromArgb(17, 20, 16), Panel = Color.FromArgb(27, 32, 24), Ink = Color.FromArgb(229, 237, 220), Muted = Color.FromArgb(154, 174, 138), Lime = Color.FromArgb(211, 250, 118);
    readonly ICtrlDebugApi api;
    readonly Action stopApplication;
    readonly bool testing;
    readonly System.Windows.Forms.Timer timer;
    readonly TextBox goal, values, pageText;
    readonly ComboBox targets;
    readonly NumericUpDown maxActions;
    readonly Label status, notice, pageHeading, traceHeading, decisionHeading, browserStatus, credentialStatus;
    readonly CtrlDebugButton run, pause, cancel, restart, stop, choose, execute, step, refresh, connect, credentials;
    readonly ToolTip hints;
    readonly TreeView tree;
    readonly ListView timeline;
    readonly DataGridView options;
    readonly CheckBox followLatest;
    Dictionary<string, object> state = new Dictionary<string, object>();
    bool initialized, rendering, online, pending, polling, stopped, targetDraft;
    int epoch, traceVersion;
    string signature = "", selectedTraceId = "", traceStamp = "";
    public CtrlDebugForm(ICtrlDebugApi client, Action stopper, bool test) {
        api = client; stopApplication = stopper; testing = test;
        Text = "[CTRL] Native Debugger";
        ClientSize = new Size(1420, 910); MinimumSize = new Size(1050, 740);
        StartPosition = FormStartPosition.CenterScreen; BackColor = Background; ForeColor = Ink;
        Font = new Font("Segoe UI", 9); AutoScaleMode = AutoScaleMode.Dpi;
        TableLayoutPanel layout = new TableLayoutPanel { Dock = DockStyle.Fill, Padding = new Padding(18), ColumnCount = 1, RowCount = 6, BackColor = Background };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 42));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 152));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 43));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 43));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        Controls.Add(layout);

        TableLayoutPanel header = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2 };
        header.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 305)); header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        Label brand = MakeLabel("[CTRL]  /  NATIVE DEBUGGER"); brand.Font = new Font("Segoe UI", 15, FontStyle.Bold); brand.ForeColor = Lime;
        status = MakeLabel("Connecting to local agent..."); status.TextAlign = ContentAlignment.MiddleRight;
        header.Controls.Add(brand, 0, 0); header.Controls.Add(status, 1, 0); layout.Controls.Add(header, 0, 0);

        TableLayoutPanel input = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2 };
        input.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 64)); input.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 36));
        input.RowStyles.Add(new RowStyle(SizeType.Percent, 70)); input.RowStyles.Add(new RowStyle(SizeType.Percent, 30));
        goal = MakeTextBox(false); goal.Multiline = true; goal.ScrollBars = ScrollBars.Vertical;
        values = MakeTextBox(false); values.Multiline = true; values.ScrollBars = ScrollBars.Vertical;
        input.Controls.Add(Group("Command in Chrome", goal), 0, 0);
        input.Controls.Add(Group("Exact text to enter (optional, one value per line)", values), 1, 0);
        targets = new ComboBox { Dock = DockStyle.Fill, DropDownStyle = ComboBoxStyle.DropDownList, BackColor = Panel, ForeColor = Ink, FlatStyle = FlatStyle.Flat };
        targets.Items.Add(new CtrlDebugTab { Id = "", Label = "Automatic - use destinations in the command" }); targets.SelectedIndex = 0;
        FlowLayoutPanel targetRow = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Padding = new Padding(0, 5, 0, 0) };
        Label targetLabel = MakeLabel("Start tab"); targetLabel.Width = 56; targetLabel.Height = 29; targetLabel.Dock = DockStyle.None;
        targets.Dock = DockStyle.None; targets.Width = 445; targets.Height = 30;
        refresh = MakeButton("Refresh tabs", 92); targetRow.Controls.Add(targetLabel); targetRow.Controls.Add(targets); targetRow.Controls.Add(refresh);
        input.Controls.Add(targetRow, 0, 1);
        FlowLayoutPanel budgetRow = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, Padding = new Padding(0, 5, 0, 0) };
        Label budgetLabel = MakeLabel("Max actions"); budgetLabel.Width = 85; budgetLabel.Height = 29; budgetLabel.Dock = DockStyle.None;
        maxActions = new NumericUpDown { Minimum = 1, Maximum = 500, Value = 200, Width = 76, BackColor = Panel, ForeColor = Ink };
        Label suppliedHint = MakeLabel("Jev selects supplied text; it does not draft prose."); suppliedHint.Width = 280; suppliedHint.Height = 29; suppliedHint.Dock = DockStyle.None; suppliedHint.Font = new Font("Segoe UI", 8);
        budgetRow.Controls.Add(budgetLabel); budgetRow.Controls.Add(maxActions); budgetRow.Controls.Add(suppliedHint); input.Controls.Add(budgetRow, 1, 1);
        layout.Controls.Add(input, 0, 1);

        TableLayoutPanel commandBar = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1 };
        commandBar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); commandBar.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 140));
        FlowLayoutPanel commands = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = false, AutoScroll = true };
        run = MakeButton("Run task", 98); run.BackColor = Lime; run.ForeColor = Background;
        pause = MakeButton("Pause", 75); cancel = MakeButton("Cancel task", 96); restart = MakeButton("Restart task", 102);
        step = MakeButton("Single step", 98); choose = MakeButton("Choose only", 102); execute = MakeButton("Execute choice", 116);
        stop = MakeButton("STOP APP", 128); stop.BackColor = Color.FromArgb(171, 44, 35); stop.ForeColor = Color.White; stop.Font = new Font("Segoe UI", 9, FontStyle.Bold);
        foreach (Control command in new Control[] { run, pause, cancel, restart, step, choose, execute }) commands.Controls.Add(command);
        stop.Dock = DockStyle.None; stop.Anchor = AnchorStyles.Top | AnchorStyles.Right; stop.Margin = new Padding(0, 2, 0, 0);
        commandBar.Controls.Add(commands, 0, 0); commandBar.Controls.Add(stop, 1, 0);
        followLatest = new CheckBox { Text = "Follow latest trace", Checked = true, AutoSize = true, Margin = new Padding(16, 9, 0, 0), ForeColor = Muted };
        layout.Controls.Add(commandBar, 0, 2);
        TableLayoutPanel connections = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 1 };
        connections.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); connections.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 118)); connections.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); connections.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 108));
        browserStatus = MakeLabel("CHROME / not checked"); credentialStatus = MakeLabel("JEV ACCESS / not checked");
        browserStatus.Font = credentialStatus.Font = new Font("Segoe UI", 8);
        connect = MakeButton("Connect Chrome", 110); credentials = MakeButton("Check access", 100);
        connections.Controls.Add(browserStatus, 0, 0); connections.Controls.Add(connect, 1, 0); connections.Controls.Add(credentialStatus, 2, 0); connections.Controls.Add(credentials, 3, 0);
        layout.Controls.Add(connections, 0, 3); hints = new ToolTip();
        notice = MakeLabel("Task controls affect the local agent. The independent emergency panel remains available."); notice.ForeColor = Muted; layout.Controls.Add(notice, 0, 4);

        SplitContainer columns = new SplitContainer { Dock = DockStyle.Fill, BackColor = Background, SplitterWidth = 8 };
        layout.Controls.Add(columns, 0, 5);
        TableLayoutPanel left = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1 };
        left.RowStyles.Add(new RowStyle(SizeType.Percent, 28)); left.RowStyles.Add(new RowStyle(SizeType.Percent, 44)); left.RowStyles.Add(new RowStyle(SizeType.Percent, 28));
        columns.Panel1.Controls.Add(left);
        pageText = MakeTextBox(true); pageText.Multiline = true; pageText.ScrollBars = ScrollBars.Vertical; pageText.Font = new Font("Consolas", 9);
        pageHeading = MakeLabel("OBSERVED PAGE"); left.Controls.Add(Group(pageHeading, pageText), 0, 0);
        tree = new TreeView { Dock = DockStyle.Fill, BackColor = Panel, ForeColor = Ink, BorderStyle = BorderStyle.None, HideSelection = false, FullRowSelect = true, ShowNodeToolTips = true };
        left.Controls.Add(Group("OBSERVATION + DECISION TREE", tree), 0, 1);
        timeline = new ListView { Dock = DockStyle.Fill, View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false, BackColor = Panel, ForeColor = Ink, BorderStyle = BorderStyle.None };
        timeline.Columns.Add("#", 36); timeline.Columns.Add("Operation", 98); timeline.Columns.Add("Execution", 90); timeline.Columns.Add("Target", 255); timeline.Columns.Add("ms", 55);
        traceHeading = MakeLabel("TRACE TIMELINE - no task started"); left.Controls.Add(Group(traceHeading, timeline), 0, 2);
        TableLayoutPanel right = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 3, ColumnCount = 1 };
        right.RowStyles.Add(new RowStyle(SizeType.Absolute, 53)); right.RowStyles.Add(new RowStyle(SizeType.Percent, 100)); right.RowStyles.Add(new RowStyle(SizeType.Absolute, 62));
        decisionHeading = MakeLabel("OFFERED OPTIONS / JEV PROBABILITIES\r\nChoose a trace to inspect its recorded decision.");
        TableLayoutPanel auditHeader = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2 };
        auditHeader.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); auditHeader.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 150));
        auditHeader.Controls.Add(decisionHeading, 0, 0); auditHeader.Controls.Add(followLatest, 1, 0); right.Controls.Add(auditHeader, 0, 0);
        options = new DataGridView { Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, AllowUserToDeleteRows = false, AllowUserToResizeRows = false, AutoGenerateColumns = false, BackgroundColor = Panel, BorderStyle = BorderStyle.None, RowHeadersVisible = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false, EnableHeadersVisualStyles = false };
        options.DefaultCellStyle.BackColor = Panel; options.DefaultCellStyle.ForeColor = Ink; options.DefaultCellStyle.SelectionBackColor = Color.FromArgb(67, 87, 44); options.DefaultCellStyle.SelectionForeColor = Color.White;
        options.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(44, 53, 35); options.ColumnHeadersDefaultCellStyle.ForeColor = Lime; options.ColumnHeadersHeight = 31; options.RowTemplate.Height = 29;
        foreach (object[] spec in new object[][] { new object[] { "Question", 91 }, new object[] { "Option / label", 245 }, new object[] { "Probability", 82 }, new object[] { "Answered", 70 }, new object[] { "Used", 47 }, new object[] { "Allowed", 100 }, new object[] { "Validation note", 250 } }) {
            DataGridViewTextBoxColumn column = new DataGridViewTextBoxColumn { HeaderText = (string)spec[0], Width = (int)spec[1], SortMode = DataGridViewColumnSortMode.NotSortable }; options.Columns.Add(column);
        }
        right.Controls.Add(options, 0, 1);
        Label explanation = MakeLabel("Answered = Jev selected this option. Used = part of the actual action.\r\nOther heads may be answered but unused. \"Not evaluated\" is not permission.\r\nThis is a recorded decision audit, not a generated reasoning transcript.");
        explanation.ForeColor = Muted; explanation.Font = new Font("Segoe UI", 8); right.Controls.Add(explanation, 0, 2); columns.Panel2.Controls.Add(right);
        Shown += delegate { columns.SplitterDistance = Math.Max(370, (int)(columns.Width * 0.43)); };
        goal.TextChanged += delegate { DraftChanged(false); }; values.TextChanged += delegate { DraftChanged(false); }; maxActions.ValueChanged += delegate { DraftChanged(false); };
        targets.SelectedIndexChanged += delegate { DraftChanged(true); };
        refresh.Click += async delegate {
            if (stopped || !online) { stopped = false; if (!testing) timer.Start(); await Poll(); }
            else await Command(async delegate { Apply(await api.Get("/api/tabs")); });
        };
        connect.Click += async delegate { await Command(async delegate { Apply(await api.Post("/api/connect", new Dictionary<string, object>())); }); };
        credentials.Click += async delegate { await Command(async delegate { Apply(await api.Post("/api/credentials", new Dictionary<string, object>())); }); };
        run.Click += async delegate { await Command(async delegate { await RunTask("run", false); }); };
        step.Click += async delegate { await Command(async delegate { await RunTask("step", false); }); };
        choose.Click += async delegate { await Command(async delegate { await RunTask("predict", false); }); };
        restart.Click += async delegate { await Command(async delegate { await RunTask("run", true); }); };
        pause.Click += async delegate { await Interrupt("/api/pause"); };
        cancel.Click += async delegate { await Interrupt("/api/cancel"); };
        execute.Click += async delegate { await Command(async delegate { if (Dirty()) throw new InvalidOperationException("The command changed. Choose a fresh action."); object fingerprint = CtrlDebugJson.Get(CtrlDebugJson.Get(state, "decision"), "fingerprint") ?? CtrlDebugJson.Get(CtrlDebugJson.Get(state, "page"), "fingerprint"); if (fingerprint == null) throw new InvalidOperationException("Choose a fresh action first."); Apply(await api.Post("/api/act", new Dictionary<string, object> { { "fingerprint", fingerprint } })); }); };
        stop.Click += delegate { StopApp(); };
        timeline.SelectedIndexChanged += async delegate {
            if (rendering || timeline.SelectedItems.Count == 0) return;
            followLatest.Checked = false; await LoadTrace(Convert.ToString(timeline.SelectedItems[0].Tag), true);
        };
        followLatest.CheckedChanged += async delegate { if (followLatest.Checked && !rendering) await LoadTrace(CtrlDebugJson.Text(state, "latest_trace_id"), true); };
        timer = new System.Windows.Forms.Timer { Interval = 700 }; timer.Tick += async delegate { await Poll(); };
        Shown += async delegate { if (!testing) { timer.Start(); await Poll(); } };
        FormClosing += delegate { timer.Stop(); epoch++; };
        UpdateButtons();
    }

    static Label MakeLabel(string text) { return new Label { Text = text, Dock = DockStyle.Fill, AutoEllipsis = true, TextAlign = ContentAlignment.MiddleLeft, ForeColor = Ink }; }
    static TextBox MakeTextBox(bool readOnly) { return new TextBox { Dock = DockStyle.Fill, ReadOnly = readOnly, BackColor = Panel, ForeColor = Ink, BorderStyle = BorderStyle.FixedSingle }; }
    static CtrlDebugButton MakeButton(string text, int width) { return new CtrlDebugButton { Text = text, Width = width, Height = 34, FlatStyle = FlatStyle.Flat, BackColor = Color.FromArgb(43, 54, 34), ForeColor = Ink, Margin = new Padding(0, 2, 7, 0) }; }
    static Control Group(string caption, Control content) { return Group(MakeLabel(caption), content); }
    static Control Group(Label caption, Control content) {
        TableLayoutPanel panel = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2, Padding = new Padding(0, 0, 7, 6) };
        panel.RowStyles.Add(new RowStyle(SizeType.Absolute, 25)); panel.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        caption.ForeColor = Muted; caption.Font = new Font("Segoe UI", 8, FontStyle.Bold); panel.Controls.Add(caption, 0, 0); panel.Controls.Add(content, 0, 1); return panel;
    }
    Dictionary<string, object> Inputs() {
        CtrlDebugTab selected = targets.SelectedItem as CtrlDebugTab;
        List<string> text = new List<string>(); foreach (string value in values.Text.Replace("\r", "").Split('\n')) if (!String.IsNullOrWhiteSpace(value)) text.Add(value);
        return new Dictionary<string, object> { { "goal", goal.Text.Trim() }, { "target_id", selected == null ? "" : selected.Id }, { "values", text.ToArray() }, { "max_steps", (int)maxActions.Value } };
    }
    bool Busy() { return CtrlDebugJson.Flag(state, "running") || CtrlDebugJson.Flag(state, "busy"); }
    bool Dirty() { return signature != CtrlDebugJson.Dump(Inputs()) || CtrlDebugJson.Text(state, "goal").Trim() != goal.Text.Trim(); }
    void DraftChanged(bool target) { if (rendering) return; if (target) targetDraft = true; if (initialized && Dirty()) notice.Text = "Edited command, tab, values, or budget: Run starts a fresh task; the old preview cannot execute."; UpdateButtons(); }
    void UpdateButtons() {
        bool available = online && !stopped, idle = available && !pending && !Busy(), hasGoal = !String.IsNullOrWhiteSpace(goal.Text);
        run.Enabled = step.Enabled = choose.Enabled = idle && hasGoal; restart.Enabled = available && !pending && hasGoal;
        pause.Enabled = available && Busy(); cancel.Enabled = available && (!String.IsNullOrEmpty(CtrlDebugJson.Text(state, "goal")) || Busy());
        execute.Enabled = idle && !Dirty() && CtrlDebugJson.Get(state, "decision") != null;
        refresh.Enabled = idle || stopped || !online; refresh.Text = stopped || !online ? "Reconnect app" : "Refresh tabs";
        connect.Enabled = credentials.Enabled = idle; stop.Enabled = true; stop.Text = stopped ? "REPEAT STOP" : "STOP APP";
        goal.ReadOnly = values.ReadOnly = Busy() || pending || stopped; targets.Enabled = maxActions.Enabled = !Busy() && !pending && !stopped;
    }
    void Apply(Dictionary<string, object> next) {
        if (stopped || IsDisposed) return;
        state = next; online = true; rendering = true;
        try {
            string previous = (targets.SelectedItem as CtrlDebugTab ?? new CtrlDebugTab { Id = "" }).Id;
            object selectedValue = CtrlDebugJson.Get(state, "selected_tab");
            string selected = selectedValue is string ? (string)selectedValue : CtrlDebugJson.Text(selectedValue, "id");
            targets.Items.Clear(); targets.Items.Add(new CtrlDebugTab { Id = "", Label = "Automatic - use destinations in the command" });
            foreach (object tab in CtrlDebugJson.Items(CtrlDebugJson.Get(state, "tabs"))) {
                string id = CtrlDebugJson.Text(tab, "id"); if (id == "") id = CtrlDebugJson.Text(tab, "target_id");
                string label = CtrlDebugJson.Text(tab, "title"); if (label == "") label = CtrlDebugJson.Text(tab, "url");
                targets.Items.Add(new CtrlDebugTab { Id = id, Label = label });
            }
            string desired = targetDraft ? previous : selected; targets.SelectedIndex = 0;
            for (int i = 0; i < targets.Items.Count; i++) if (((CtrlDebugTab)targets.Items[i]).Id == desired) { targets.SelectedIndex = i; break; }
            if (!initialized) {
                if (goal.Text.Length == 0 && CtrlDebugJson.Text(state, "goal") != "") {
                    goal.Text = CtrlDebugJson.Text(state, "goal");
                    List<string> restored = new List<string>(); foreach (object item in CtrlDebugJson.Items(CtrlDebugJson.Get(state, "values"))) restored.Add(Convert.ToString(item));
                    values.Text = String.Join(Environment.NewLine, restored);
                    int budget; if (Int32.TryParse(CtrlDebugJson.Text(state, "max_steps"), out budget)) maxActions.Value = Math.Max(1, Math.Min(500, budget));
                    signature = CtrlDebugJson.Dump(Inputs());
                }
                initialized = true;
            } else if (!targetDraft && signature != "") {
                Dictionary<string, object> saved = CtrlDebugJson.Parse(signature);
                if (CtrlDebugJson.Text(saved, "goal") == CtrlDebugJson.Text(state, "goal")) { saved["target_id"] = desired; signature = CtrlDebugJson.Dump(saved); }
            }
            string mode = CtrlDebugJson.Text(state, "status").Replace('_', ' ');
            object browser = CtrlDebugJson.Get(state, "browser"), access = CtrlDebugJson.Get(state, "credentials");
            browserStatus.Text = "CHROME / " + CtrlDebugJson.Text(browser, "status").Replace('_', ' ') + "\r\n" + CtrlDebugJson.Text(browser, "message");
            credentialStatus.Text = "JEV ACCESS / " + CtrlDebugJson.Text(access, "status").Replace('_', ' ') + "\r\n" + CtrlDebugJson.Text(access, "message");
            hints.SetToolTip(browserStatus, browserStatus.Text); hints.SetToolTip(credentialStatus, credentialStatus.Text);
            status.Text = (CtrlDebugJson.Flag(state, "running") ? "RUNNING" : mode.ToUpperInvariant()) + "  |  " + CtrlDebugJson.Items(CtrlDebugJson.Get(state, "history")).Count + " actions";
            string reason = CtrlDebugJson.Text(state, "error"); if (reason == "") reason = CtrlDebugJson.Text(state, "stop_reason"); if (reason == "") reason = CtrlDebugJson.Text(state, "input_request");
            notice.Text = reason != "" ? reason : "Choose previews one decision. Execute validates that preview against the current page. Cancel preserves the trace.";
            notice.ForeColor = reason == "" ? Muted : Color.FromArgb(242, 189, 144);
            List<object> summaries = CtrlDebugJson.Items(CtrlDebugJson.Get(state, "trace_summaries"));
            string newStamp = CtrlDebugJson.Dump(summaries);
            if (newStamp != traceStamp) {
                traceStamp = newStamp; timeline.BeginUpdate(); timeline.Items.Clear();
                foreach (object summary in summaries) {
                    ListViewItem item = new ListViewItem(new string[] { CtrlDebugJson.Text(summary, "sequence"), CtrlDebugJson.Text(summary, "operation"), CtrlDebugJson.Text(summary, "status"), CtrlDebugJson.Text(summary, "target_label"), CtrlDebugJson.Text(summary, "latency_ms") });
                    item.Tag = CtrlDebugJson.Text(summary, "id"); timeline.Items.Add(item);
                    if ((followLatest.Checked && Convert.ToString(item.Tag) == CtrlDebugJson.Text(state, "latest_trace_id")) || (!followLatest.Checked && Convert.ToString(item.Tag) == selectedTraceId)) item.Selected = true;
                }
                timeline.EndUpdate(); traceHeading.Text = "TRACE TIMELINE - " + summaries.Count + " recorded decisions";
                if (followLatest.Checked) { string latest = CtrlDebugJson.Text(state, "latest_trace_id"); if (latest != "") { Task unused = LoadTrace(latest, true); } }
            }
            if (summaries.Count == 0) { traceVersion++; selectedTraceId = ""; RenderObservation(CtrlDebugJson.Get(state, "page"), null); options.Rows.Clear(); decisionHeading.Text = "OFFERED OPTIONS / JEV PROBABILITIES\r\nNo decision recorded for this task."; }
        } finally { rendering = false; UpdateButtons(); }
    }
    async Task Command(Func<Task> action) {
        if (pending || stopped) return; pending = true; UpdateButtons();
        try { await action(); } catch (Exception error) { if (!IsDisposed) { notice.Text = error.Message; notice.ForeColor = Color.FromArgb(247, 162, 144); } }
        finally { pending = false; if (!IsDisposed) UpdateButtons(); }
    }
    async Task<bool> WaitIdle(int generation) {
        DateTime deadline = DateTime.UtcNow.AddSeconds(60);
        while (Busy() && !stopped && generation == epoch) {
            if (DateTime.UtcNow > deadline) throw new InvalidOperationException("The current request is still working. Pause, Cancel, or Stop remains available.");
            await Task.Delay(200); if (stopped || generation != epoch) return false; Apply(await api.Get("/api/state"));
        }
        return !stopped && generation == epoch;
    }
    async Task RunTask(string mode, bool forceRestart) {
        Dictionary<string, object> input = Inputs(); if (CtrlDebugJson.Text(input, "goal") == "") throw new InvalidOperationException("Enter a command first.");
        int generation = ++epoch;
        if (forceRestart && CtrlDebugJson.Text(state, "goal") != "") { Apply(await api.Post("/api/cancel", new Dictionary<string, object>())); if (!await WaitIdle(generation)) return; }
        string taskStatus = CtrlDebugJson.Text(state, "status");
        bool startNew = forceRestart || Dirty() || Regex.IsMatch(taskStatus, "^(done|blocked|error|cancelled)$") || CtrlDebugJson.Text(state, "goal") == "";
        if (startNew) {
            input["run"] = mode == "run";
            Apply(await api.Post("/api/start", input)); input.Remove("run"); signature = CtrlDebugJson.Dump(input); targetDraft = false;
            if (mode == "run" || !await WaitIdle(generation)) return;
        }
        if (!stopped && generation == epoch) Apply(await api.Post("/api/" + mode, new Dictionary<string, object>()));
    }
    async Task Interrupt(string path) { if (stopped) return; epoch++; try { Apply(await api.Post(path, new Dictionary<string, object>())); } catch (Exception error) { if (!IsDisposed) notice.Text = error.Message; } }
    void StopApp() { stopped = true; epoch++; timer.Stop(); UpdateButtons(); status.Text = "STOP REQUESTED"; notice.Text = "Stop requested through the independent script. After restarting the app from CTRL Control Panel, click Reconnect app.";
        try { stopApplication(); } catch (Exception error) { stopped = false; notice.Text = "Stop could not be launched: " + error.Message; if (!testing) timer.Start(); UpdateButtons(); }
    }
    async Task Poll() {
        if (polling || stopped || IsDisposed) return; polling = true;
        try { Apply(await api.Get("/api/state")); }
        catch { if (!IsDisposed) { online = false; status.Text = "LOCAL APP UNAVAILABLE"; notice.Text = "Start CTRL separately, then this window will reconnect. No task is submitted automatically."; UpdateButtons(); } }
        finally { polling = false; }
    }
    async Task LoadTrace(string id, bool force) {
        if (id == "" || stopped || (!force && selectedTraceId == id)) return;
        int version = ++traceVersion;
        try { Dictionary<string, object> detail = await api.Get("/api/traces/" + Uri.EscapeDataString(id)); if (version != traceVersion || stopped || IsDisposed) return; selectedTraceId = id; RenderTrace(detail); }
        catch (Exception error) { if (version == traceVersion && !IsDisposed) notice.Text = "Trace unavailable: " + error.Message; }
    }
    void RenderTrace(Dictionary<string, object> detail) {
        object selected = CtrlDebugJson.Get(detail, "selected"), validation = CtrlDebugJson.Get(detail, "validation"), execution = CtrlDebugJson.Get(detail, "execution");
        decisionHeading.Text = "JEV / " + CtrlDebugJson.Text(selected, "operation") + " -> " + CtrlDebugJson.Text(selected, "target_label") + "\r\nValidation: " + CtrlDebugJson.Text(validation, "status") + "   Execution: " + CtrlDebugJson.Text(execution, "status") + "   " + CtrlDebugJson.Text(detail, "latency_ms") + " ms";
        options.Rows.Clear();
        foreach (object question in CtrlDebugJson.Items(CtrlDebugJson.Get(detail, "questions"))) foreach (object option in CtrlDebugJson.Items(CtrlDebugJson.Get(question, "options"))) {
            bool answered = CtrlDebugJson.Flag(option, "selected"), used = CtrlDebugJson.Flag(option, "used");
            int row = options.Rows.Add(CtrlDebugJson.Text(question, "id"), CtrlDebugJson.Text(option, "id") + "  " + CtrlDebugJson.Text(option, "label"), CtrlDebugJson.Probability(CtrlDebugJson.Get(option, "probability")), answered ? "Yes" : "", used ? "Yes" : "", CtrlDebugJson.Allowed(CtrlDebugJson.Get(option, "allowed")), CtrlDebugJson.Text(option, "validation_note"));
            if (used && answered) { options.Rows[row].DefaultCellStyle.BackColor = Color.FromArgb(55, 78, 34); options.Rows[row].DefaultCellStyle.ForeColor = Lime; }
            else if (answered) options.Rows[row].DefaultCellStyle.ForeColor = Color.FromArgb(229, 194, 136);
            else options.Rows[row].DefaultCellStyle.ForeColor = Muted;
            if (Object.Equals(CtrlDebugJson.Get(option, "allowed"), false)) options.Rows[row].DefaultCellStyle.ForeColor = Color.FromArgb(255, 167, 155);
        }
        options.ClearSelection(); RenderObservation(CtrlDebugJson.Get(detail, "observation"), detail);
    }
    void RenderObservation(object observation, object detail) {
        pageHeading.Text = CtrlDebugJson.Text(observation, "title") + "  |  " + CtrlDebugJson.Text(observation, "url");
        if (pageHeading.Text == "  |  ") pageHeading.Text = "OBSERVED PAGE - awaiting a task";
        pageText.Text = CtrlDebugJson.Text(observation, "text").Replace("\r\n", "\n").Replace("\n", Environment.NewLine);
        tree.BeginUpdate(); tree.Nodes.Clear();
        TreeNode root = tree.Nodes.Add("Observed page: " + CtrlDebugJson.Text(observation, "title")); root.ToolTipText = CtrlDebugJson.Text(observation, "url");
        object selected = CtrlDebugJson.Get(detail, "selected"); string actionId = CtrlDebugJson.Text(selected, "action_id");
        List<object> actions = CtrlDebugJson.Items(CtrlDebugJson.Get(observation, "actions"));
        TreeNode controls = root.Nodes.Add("Observed controls / available actions (" + actions.Count + ")");
        foreach (object action in actions) {
            string id = CtrlDebugJson.Text(action, "id"), label = CtrlDebugJson.Text(action, "label"); if (label == "") label = CtrlDebugJson.Text(action, "text");
            TreeNode node = controls.Nodes.Add("[" + id + "] " + CtrlDebugJson.Text(action, "kind") + "  " + label);
            if (id == actionId && id != "") { node.BackColor = Color.FromArgb(55, 78, 34); node.ForeColor = Lime; }
        }
        if (detail != null) {
            TreeNode branch = tree.Nodes.Add("Decision: " + CtrlDebugJson.Text(selected, "operation") + " -> " + CtrlDebugJson.Text(selected, "target_label"));
            foreach (object question in CtrlDebugJson.Items(CtrlDebugJson.Get(detail, "questions"))) {
                TreeNode head = branch.Nodes.Add(CtrlDebugJson.Text(question, "label") + (CtrlDebugJson.Flag(question, "applies") ? " [active head]" : " [unused head]"));
                foreach (object option in CtrlDebugJson.Items(CtrlDebugJson.Get(question, "options"))) {
                    bool answered = CtrlDebugJson.Flag(option, "selected"), used = CtrlDebugJson.Flag(option, "used");
                    TreeNode node = head.Nodes.Add((answered ? "* " : "") + CtrlDebugJson.Text(option, "id") + "  " + CtrlDebugJson.Text(option, "label") + "  |  " + CtrlDebugJson.Probability(CtrlDebugJson.Get(option, "probability")) + "  |  " + CtrlDebugJson.Allowed(CtrlDebugJson.Get(option, "allowed")));
                    node.ToolTipText = CtrlDebugJson.Text(option, "validation_note");
                    if (used && answered) { node.BackColor = Color.FromArgb(55, 78, 34); node.ForeColor = Lime; head.Expand(); }
                    else node.ForeColor = answered ? Color.FromArgb(229, 194, 136) : Muted;
                }
            }
            branch.Expand();
        }
        root.Expand(); controls.Expand(); tree.EndUpdate();
    }
    protected override bool ShowWithoutActivation { get { return testing; } }
    protected override void Dispose(bool disposing) { if (disposing) { timer.Stop(); timer.Dispose(); hints.Dispose(); IDisposable disposable = api as IDisposable; if (disposable != null) disposable.Dispose(); } base.Dispose(disposing); }

    public void Verify(CtrlDebugFakeApi fake, string preview) {
        ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; Location = new Point(-32000, -32000); Show(); Application.DoEvents();
        Apply(fake.State); RenderTrace(fake.Trace);
        if (fake.Posts.Count != 0) throw new InvalidOperationException("Opening the debugger submitted a task.");
        if (!browserStatus.Text.Contains("waiting for approval") || !credentialStatus.Text.Contains("Synthetic sign-in expired")) throw new InvalidOperationException("Connection status was not visible.");
        connect.TestClick(); credentials.TestClick();
        if (fake.Posts.Count != 2 || fake.Posts[0].Key != "/api/connect" || fake.Posts[1].Key != "/api/credentials" || !browserStatus.Text.Contains("connected") || !credentialStatus.Text.Contains("ready")) throw new InvalidOperationException("Connection retry controls failed their synthetic routes.");
        fake.Posts.Clear();
        if (options.Rows.Count != 6 || Convert.ToString(options.Rows[0].Cells[2].Value) != "80.0%" || Convert.ToString(options.Rows[2].Cells[5].Value) != "Not evaluated" || Convert.ToString(options.Rows[5].Cells[5].Value) != "Rejected") throw new InvalidOperationException("Probability or tri-state audit rendering failed.");
        if (Convert.ToString(options.Rows[4].Cells[3].Value) != "Yes" || Convert.ToString(options.Rows[4].Cells[4].Value) != "") throw new InvalidOperationException("An unused answered head was presented as an action.");
        goal.Text = "Open https://example.test and inspect the search controls"; targets.SelectedIndex = 0; maxActions.Value = 300;
        RunTask("run", false).GetAwaiter().GetResult();
        if (fake.Posts.Count != 1 || fake.Posts[0].Key != "/api/start" || CtrlDebugJson.Text(fake.Posts[0].Value, "target_id") != "" || Convert.ToInt32(fake.Posts[0].Value["max_steps"]) != 300) throw new InvalidOperationException("New task input or optional target routing failed.");
        Interrupt("/api/cancel").GetAwaiter().GetResult(); if (fake.Posts[1].Key != "/api/cancel") throw new InvalidOperationException("Cancel routing failed.");
        RunTask("run", true).GetAwaiter().GetResult();
        if (fake.Posts[fake.Posts.Count - 2].Key != "/api/cancel" || fake.Posts[fake.Posts.Count - 1].Key != "/api/start") throw new InvalidOperationException("Explicit restart did not cancel then start once.");
        Interrupt("/api/pause").GetAwaiter().GetResult();
        if (fake.Posts[fake.Posts.Count - 1].Key != "/api/pause" || timeline.Items.Count != 1) throw new InvalidOperationException("Pause routing or retained trace history failed.");
        Apply(fake.Fixture()); RenderTrace(fake.Trace); goal.Text = "Search this page for \"release notes\", then inspect the matching result.";
        int beforeStop = fake.Posts.Count; stop.TestClick();
        if (!stopped || run.Enabled || fake.StopCalls != 1 || fake.Posts.Count != beforeStop || !refresh.Enabled || refresh.Text != "Reconnect app") throw new InvalidOperationException("Stop did not use the independent stub or left Run enabled.");
        stop.TestClick();
        if (!stop.Enabled || fake.StopCalls != 2 || stop.Text != "REPEAT STOP") throw new InvalidOperationException("Stop was not independently retryable.");
        if (!pageText.Text.Contains(Environment.NewLine)) throw new InvalidOperationException("Observed page line breaks were not preserved.");
        stopped = false; online = true; status.Text = "PAUSED  |  SYNTHETIC VERIFICATION"; notice.Text = "Synthetic self-test fixture: recorded probabilities and validation states are displayed without generated reasoning."; UpdateButtons();
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(preview)));
        Size regularSize = ClientSize; ClientSize = new Size(1034, 701); PerformLayout(); Application.DoEvents();
        Rectangle stopBounds = RectangleToClient(stop.RectangleToScreen(stop.ClientRectangle));
        Rectangle commandBounds = RectangleToClient(stop.Parent.RectangleToScreen(stop.Parent.ClientRectangle));
        if (stopBounds.Right > ClientSize.Width || stopBounds.Left < 0 || stopBounds.Width < 100 || stopBounds.Bottom > commandBounds.Bottom || stop.Height < 30) throw new InvalidOperationException("Stop is clipped at the minimum window size.");
        string minimumPreview = Path.Combine(Path.GetDirectoryName(Path.GetFullPath(preview)), Path.GetFileNameWithoutExtension(preview) + ".minimum.png");
        using (Bitmap bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(minimumPreview, ImageFormat.Png); }
        ClientSize = regularSize; PerformLayout(); Application.DoEvents();
        using (Bitmap bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(0, 0, Width, Height)); bitmap.Save(preview, ImageFormat.Png); }
        Hide();
    }
}

public sealed class CtrlDebugFakeApi : ICtrlDebugApi {
    public Dictionary<string, object> State;
    public readonly Dictionary<string, object> Trace;
    public readonly List<KeyValuePair<string, Dictionary<string, object>>> Posts = new List<KeyValuePair<string, Dictionary<string, object>>>();
    public int StopCalls;
    public CtrlDebugFakeApi() {
        Trace = CtrlDebugJson.Parse(@"{""id"":""fixture-1"",""sequence"":1,""model"":""typesafe/jev-1.13"",""latency_ms"":184,""observation"":{""title"":""Synthetic documentation page"",""url"":""https://example.test/docs"",""text"":""Documentation\nSearch release notes\nVersion history\nThis is synthetic data used only for the offline UI test."",""actions"":[{""id"":""e1"",""kind"":""fill"",""label"":""Search documentation""},{""id"":""e2"",""kind"":""click"",""label"":""Release notes""},{""id"":""e3"",""kind"":""click"",""label"":""Version history""}]},""questions"":[{""id"":""operation"",""label"":""Next operation"",""applies"":true,""options"":[{""id"":""CLICK"",""label"":""Click an observed control"",""probability"":0.8,""selected"":true,""used"":true,""allowed"":true,""validation_note"":""Selected branch passed runtime validation""},{""id"":""FILL"",""label"":""Enter a supplied value"",""probability"":0.2,""selected"":false,""used"":false,""allowed"":null,""validation_note"":""Not selected""}]},{""id"":""click_target"",""label"":""Click target"",""applies"":true,""options"":[{""id"":""e3"",""label"":""Version history"",""probability"":0.09,""selected"":false,""used"":false,""allowed"":null},{""id"":""e2"",""label"":""Release notes"",""probability"":0.91,""selected"":true,""used"":true,""allowed"":true,""validation_note"":""Observed identity and freshness validated""}]},{""id"":""fill_target"",""label"":""Unused fill target"",""applies"":false,""options"":[{""id"":""e1"",""label"":""Search documentation"",""probability"":1.0,""selected"":true,""used"":false,""allowed"":null,""validation_note"":""Answered in parallel; unused by CLICK""}]},{""id"":""rejected_example"",""label"":""Synthetic rejected branch"",""applies"":false,""options"":[{""id"":""stale"",""label"":""Example stale target"",""probability"":null,""selected"":false,""used"":false,""allowed"":false,""validation_note"":""Synthetic rejection fixture""}]}],""selected"":{""operation"":""CLICK"",""action_id"":""e2"",""target_id"":""e2"",""target_label"":""Release notes""},""validation"":{""status"":""passed"",""message"":""Fresh observed target""},""execution"":{""status"":""executed"",""message"":""Click recorded""}}");
        State = Fixture();
    }
    public Dictionary<string, object> Fixture() {
        Dictionary<string, object> fixture = CtrlDebugJson.Parse(@"{""status"":""paused"",""running"":false,""busy"":false,""goal"":""Search this page for \""release notes\"", then inspect the matching result."",""values"":[""release notes""],""max_steps"":200,""selected_tab"":""sample-tab"",""tabs"":[{""id"":""sample-tab"",""title"":""Synthetic documentation page""}],""history"":[{""action"":""Click Release notes""}],""trace_summaries"":[{""id"":""fixture-1"",""sequence"":1,""status"":""executed"",""operation"":""CLICK"",""target_label"":""Release notes"",""latency_ms"":184}],""latest_trace_id"":""fixture-1"",""decision"":null}");
        fixture["page"] = CtrlDebugJson.Get(Trace, "observation");
        fixture["browser"] = new Dictionary<string, object> { { "status", "waiting_for_approval" }, { "message", "Synthetic fixture: approve Chrome's connection prompt." } };
        fixture["credentials"] = new Dictionary<string, object> { { "status", "error" }, { "message", "Synthetic sign-in expired. Sign in again, then check access." } };
        return fixture;
    }
    public Task<Dictionary<string, object>> Get(string path) { return Task.FromResult(path.StartsWith("/api/traces/") ? Trace : State); }
    public Task<Dictionary<string, object>> Post(string path, Dictionary<string, object> body) {
        Posts.Add(new KeyValuePair<string, Dictionary<string, object>>(path, CtrlDebugJson.Parse(CtrlDebugJson.Dump(body))));
        if (path == "/api/start") { State["goal"] = body["goal"]; State["max_steps"] = body["max_steps"]; State["selected_tab"] = body["target_id"]; State["status"] = "ready"; }
        if (path == "/api/cancel") State["status"] = "cancelled";
        if (path == "/api/pause") State["status"] = "paused";
        if (path == "/api/connect") State["browser"] = new Dictionary<string, object> { { "status", "connected" }, { "message", "Synthetic Chrome connection ready." } };
        if (path == "/api/credentials") State["credentials"] = new Dictionary<string, object> { { "status", "ready" }, { "message", "Synthetic Jev access check passed." } };
        State["busy"] = false; State["running"] = false; return Task.FromResult(State);
    }
}

public static class CtrlDebugApp {
    public static void Run(string root) {
        Application.EnableVisualStyles();
        string hash;
        using (SHA256 sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(root).ToLowerInvariant()))).Replace("-", "").Substring(0, 16);
        string mutexName = "Local\\CTRL-Debugger-" + hash;
        bool created; using (Mutex mutex = new Mutex(true, mutexName, out created)) {
            if (!created) return;
            try {
                using (CtrlDebugForm form = new CtrlDebugForm(new CtrlDebugApi(), delegate {
                    string powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
                    Process.Start(new ProcessStartInfo(powershell, "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(root, "Stop-CTRL.ps1") + "\" -Force") { UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden, WorkingDirectory = root });
                }, false)) Application.Run(form);
            } finally { mutex.ReleaseMutex(); }
        }
    }
    public static string Verify(string preview) {
        Application.EnableVisualStyles(); CtrlDebugFakeApi fake = new CtrlDebugFakeApi();
        using (CtrlDebugForm form = new CtrlDebugForm(fake, delegate { fake.StopCalls++; }, true)) form.Verify(fake, preview);
        return CtrlDebugJson.Dump(new Dictionary<string, object> { { "ok", true }, { "synthetic_only", true }, { "all_options_rendered", true }, { "tri_state_validation", true }, { "unused_answers_distinct", true }, { "empty_target_accepted", true }, { "cancel_then_restart", true }, { "no_auto_submit", true }, { "connection_controls", true }, { "stop_visible_at_minimum_size", true }, { "stop_stub_calls", fake.StopCalls }, { "preview_path", Path.GetFullPath(preview) } });
    }
}
