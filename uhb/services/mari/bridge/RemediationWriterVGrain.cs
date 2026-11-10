using MARI.Framework.Core;
using MARI.Framework.Core.Abstractions;
using MARI.Framework.Core.Abstractions.Signals;
using Microsoft.Extensions.Logging;

namespace MARI.Bridge;

/// <summary>
/// Writes a timestamped remediation request file into C:/mari-poc/remediation/ when the fail door fires.
/// </summary>
public sealed class RemediationWriterVGrain : VGrainBase<IRemediationWriterVGrain>, IRemediationWriterVGrain, IGrainWithStringKey
{
    private const string RemediationDir = @"C:/mari-poc/remediation";

    public RemediationWriterVGrain(ILogger<IRemediationWriterVGrain> logger)
        : base(logger)
    {
    }

    public Task WriteAsync(SignalMessage signal, IExecutionContext<string> executionContext)
    {
        Directory.CreateDirectory(RemediationDir);
        var ts = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
        var file = Path.Combine(RemediationDir, $"demande_{signal.Uid}_{ts}.txt");
        File.WriteAllText(file, $"{DateTime.UtcNow:O} | fireId={signal.Uid}");
        return Task.CompletedTask;
    }
}