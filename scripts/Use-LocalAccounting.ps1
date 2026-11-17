# Load this user's explicitly configured accounting endpoint into this shell.
# The application password is in libpq's local password file, not this script.
foreach ($accountingKey in @('TECHSCOUT_ACCOUNTING_URL', 'TECHSCOUT_ACCOUNTING_PROJECT')) {
    $accountingValue = [Environment]::GetEnvironmentVariable($accountingKey, 'User')
    if ([string]::IsNullOrWhiteSpace($accountingValue)) {
        throw ('Local accounting is not configured: ' + $accountingKey)
    }
    [Environment]::SetEnvironmentVariable($accountingKey, $accountingValue, 'Process')
}
Write-Host 'Accounting configuration loaded. No credentials printed.'
