using MARI.Framework.Core;
using MARI.Framework.Core.Abstractions;
using MARI.Framework.Core.Abstractions.Signals;
using Microsoft.Extensions.Logging;

namespace MARI.Bridge;

/// <summary>
/// Writes a timestamped line into C:/mari-poc/journal/journal.log when the door fires.
/// </summary>
public sealed class JournalWriterVGrain : VGrainBase<IJournalWriterVGrain>, IJournalWriterVGrain, IGrainWithStringKey
{
    private const string JournalPath = @"C:/mari-poc/journal/journal.log";

    public JournalWriterVGrain(ILogger<IJournalWriterVGrain> logger)
        : base(logger)
    {
    }

    public Task WriteAsync(SignalMessage signal, IExecutionContext<string> executionContext)
    {
        var line = $"[{DateTime.UtcNow:O}] door-fired fireId={signal.Uid}";
        File.AppendAllText(JournalPath, line + Environment.NewLine);
        return Task.CompletedTask;
    }
}