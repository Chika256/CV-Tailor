param(
    [Parameter(Mandatory = $true)][string]$DocumentPath,
    [Parameter(Mandatory = $true)][string]$LayoutPath,
    [string]$PdfPath = ''
)

# Opens a DOCX read-only in Word, optionally exports a PDF, and writes the page count plus the
# page each paragraph starts and ends on. Nothing in the document is modified.
$ErrorActionPreference = 'Stop'
$word = $null
$document = $null

try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($DocumentPath, $false, $true)

    $paragraphPages = @()
    for ($i = 1; $i -le $document.Paragraphs.Count; $i++) {
        $paragraphRange = $document.Paragraphs.Item($i).Range
        $length = [int]$paragraphRange.Text.Trim().Length
        $startRange = $document.Range($paragraphRange.Start, $paragraphRange.Start)
        $endPosition = [Math]::Max($paragraphRange.Start, $paragraphRange.End - 1)
        $endRange = $document.Range($endPosition, $endPosition)
        # wdActiveEndPageNumber = 3
        $paragraphPages += [ordered]@{
            index = $i
            start_page = [int]$startRange.Information(3)
            end_page = [int]$endRange.Information(3)
            is_list = ([int]$paragraphRange.ListFormat.ListType -ne 0)
            length = $length
        }
    }

    $layout = [ordered]@{
        schema_version = 1
        backend = 'word'
        page_count = [int]$document.ComputeStatistics(2)
        paragraphs = $paragraphPages
    }
    $layoutJson = $layout | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText($LayoutPath, $layoutJson + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))

    if ($PdfPath) {
        # wdExportFormatPDF = 17
        $document.ExportAsFixedFormat($PdfPath, 17)
    }
}
finally {
    if ($null -ne $document) { $document.Close($false) }
    if ($null -ne $word) { $word.Quit() }
    if ($null -ne $document) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) }
    if ($null -ne $word) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
