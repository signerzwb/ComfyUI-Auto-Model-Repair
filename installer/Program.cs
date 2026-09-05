using System;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("神都猫 ComfyUI 工作流助手安装器")]
[assembly: AssemblyProduct("神都猫 ComfyUI 工作流助手")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class Program
{
    private const string PayloadName = "plugin_payload.zip";

    [STAThread]
    private static int Main(string[] args)
    {
        bool testMode = args.Length > 0 && (args[0] == "--verify-payload" || args[0] == "--install-for-test");
        try
        {
            if (args.Length == 1 && args[0] == "--verify-payload")
            {
                VerifyPayload();
                return 0;
            }
            if (args.Length == 2 && args[0] == "--install-for-test")
            {
                if (!InstallerForm.IsComfyRoot(args[1])) throw new InvalidOperationException("测试目录不是 ComfyUI 根目录。");
                InstallerForm.ExtractPayload(Path.Combine(args[1], "custom_nodes", InstallerForm.PluginFolderName));
                return 0;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new InstallerForm());
            return 0;
        }
        catch (Exception error)
        {
            if (testMode) return 1;
            MessageBox.Show("安装器无法启动：\r\n" + error.Message, "神都猫工作流助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static Stream OpenPayload()
    {
        Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(PayloadName);
        if (stream == null) throw new InvalidOperationException("安装包内容缺失或已损坏。");
        return stream;
    }

    internal static void VerifyPayload()
    {
        using (Stream stream = OpenPayload())
        using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            if (archive.Entries.Count < 3) throw new InvalidOperationException("安装包内容不完整。");
        }
    }
}

internal sealed class InstallerForm : Form
{
    internal const string PluginFolderName = "ComfyUI-Auto-Model-Repair";
    private readonly Label heading = new Label();
    private readonly Label detail = new Label();
    private readonly TextBox directory = new TextBox();
    private readonly Button browse = new Button();
    private readonly Button back = new Button();
    private readonly Button next = new Button();
    private readonly Button cancel = new Button();
    private int stage;

    internal InstallerForm()
    {
        Text = "神都猫 ComfyUI 工作流助手 v1.0 安装向导";
        ClientSize = new Size(620, 330);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 9F);

        heading.AutoSize = false;
        heading.Font = new Font("Microsoft YaHei UI", 16F, FontStyle.Bold);
        heading.ForeColor = Color.FromArgb(142, 91, 22);
        heading.Location = new Point(28, 28);
        heading.Size = new Size(560, 42);

        detail.AutoSize = false;
        detail.Location = new Point(30, 84);
        detail.Size = new Size(558, 130);

        directory.Location = new Point(30, 215);
        directory.Size = new Size(446, 28);
        directory.Text = DetectComfyRoot();

        browse.Location = new Point(486, 214);
        browse.Size = new Size(100, 30);
        browse.Text = "浏览…";
        browse.Click += delegate { BrowseForComfyRoot(); };

        back.Location = new Point(274, 278);
        back.Size = new Size(92, 30);
        back.Text = "上一步";
        back.Click += delegate { SetStage(0); };

        next.Location = new Point(374, 278);
        next.Size = new Size(104, 30);
        next.Click += delegate { Advance(); };

        cancel.Location = new Point(486, 278);
        cancel.Size = new Size(100, 30);
        cancel.Text = "取消";
        cancel.Click += delegate { Close(); };

        Controls.AddRange(new Control[] { heading, detail, directory, browse, back, next, cancel });
        SetStage(0);
    }

    private static string DetectComfyRoot()
    {
        string[] candidates = {
            @"E:\comfyui", @"C:\AI\comfyui", @"C:\ComfyUI", @"D:\ComfyUI",
            Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "ComfyUI")
        };
        foreach (string candidate in candidates)
            if (IsComfyRoot(candidate)) return candidate;
        return string.Empty;
    }

    internal static bool IsComfyRoot(string path)
    {
        return !string.IsNullOrWhiteSpace(path) && File.Exists(Path.Combine(path, "main.py"));
    }

    private void SetStage(int value)
    {
        stage = value;
        bool choosing = stage == 1;
        directory.Visible = choosing;
        browse.Visible = choosing;
        back.Visible = choosing;

        if (stage == 0)
        {
            heading.Text = "欢迎使用神都猫工作流助手";
            detail.Text = "此向导会把“神都猫 ComfyUI 工作流助手 v1.0”安装到已有的 ComfyUI。\r\n\r\n它会检查工作流、修复缺失节点与模型、下载经验证的官方文件，并按真实执行顺序整理画布。\r\n\r\n安装器只会写入本插件目录，不会改动你的模型、工作流或 ComfyUI 核心文件。";
            next.Text = "下一步";
        }
        else if (stage == 1)
        {
            heading.Text = "选择 ComfyUI 目录";
            detail.Text = "安装器已自动查找常见的 ComfyUI 目录。请确认或点击“浏览”选择 ComfyUI 根目录。\r\n\r\n正确的目录中必须包含 main.py，例如：E:\\comfyui\r\n\r\n插件会安装到：custom_nodes\\ComfyUI-Auto-Model-Repair";
            next.Text = "安装";
        }
        else
        {
            heading.Text = "安装完成";
            detail.Text = "神都猫 ComfyUI 工作流助手已安装完成。\r\n\r\n请关闭并重新启动 ComfyUI，然后在左侧栏打开“神都猫工作流助手”。\r\n\r\n加入 AI 讨论QQ群：340983417";
            next.Text = "完成";
            cancel.Visible = false;
        }
    }

    private void Advance()
    {
        if (stage == 0) { SetStage(1); return; }
        if (stage == 1) { Install(); return; }
        Close();
    }

    private void BrowseForComfyRoot()
    {
        using (FolderBrowserDialog dialog = new FolderBrowserDialog())
        {
            dialog.Description = "选择包含 main.py 的 ComfyUI 根目录";
            dialog.SelectedPath = Directory.Exists(directory.Text) ? directory.Text : DetectComfyRoot();
            if (dialog.ShowDialog(this) == DialogResult.OK) directory.Text = dialog.SelectedPath;
        }
    }

    private void Install()
    {
        string root = directory.Text.Trim();
        if (!IsComfyRoot(root))
        {
            MessageBox.Show(this, "请选择正确的 ComfyUI 根目录：其中必须包含 main.py。", "目录不正确", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            return;
        }

        try
        {
            ExtractPayload(Path.Combine(root, "custom_nodes", PluginFolderName));
            SetStage(2);
        }
        catch (UnauthorizedAccessException)
        {
            MessageBox.Show(this, "没有写入权限。请把 ComfyUI 安装在可写目录，或以管理员身份重新运行安装器。", "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch (Exception error)
        {
            MessageBox.Show(this, "安装失败：\r\n" + error.Message, "安装失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    internal static void ExtractPayload(string plugin)
    {
        string root = Path.GetFullPath(plugin).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        Directory.CreateDirectory(root);
        string[] legacy = { "README.txt", "config.json", "downloader.py", "matcher.py", "resolver.py", "source_search.py", "workflow_utils.py", "web\\model_repair.js" };
        foreach (string file in legacy)
        {
            string candidate = Path.Combine(root, file);
            if (File.Exists(candidate)) File.Delete(candidate);
        }

        using (Stream stream = Program.OpenPayload())
        using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string target = Path.GetFullPath(Path.Combine(root, entry.FullName));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("安装包包含不安全路径。");
                if (entry.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                using (Stream input = entry.Open())
                using (FileStream output = new FileStream(target, FileMode.Create, FileAccess.Write, FileShare.None))
                    input.CopyTo(output);
            }
        }
    }
}
