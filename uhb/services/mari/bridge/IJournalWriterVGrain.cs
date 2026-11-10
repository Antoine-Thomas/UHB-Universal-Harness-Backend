using MARI.Framework.Core.Abstractions;
using MARI.Framework.Core.Abstractions.Attributes;
using MARI.Framework.Core.Abstractions.Enums;
using MARI.Framework.Core.Abstractions.Signals;

namespace MARI.Bridge;

/// <summary>
/// VGrain in charge to write a journal line when a door triggers.
/// </summary>
[VGrainIdFormat(IdFormatTypeEnum.String, FirstParameterTemplate = "journal")]
public interface IJournalWriterVGrain : IVGrain
{
    Task WriteAsync(SignalMessage signal, IExecutionContext<string> executionContext);
}