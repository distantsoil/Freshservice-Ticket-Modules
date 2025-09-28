$scriptPath = Join-Path (Split-Path -Path $PSScriptRoot -Parent) "FetchAndAnalyze.ps1"
. $scriptPath

Describe "Analyse-Tickets" {
    It "suggests multi-word and short-token categories" {
        $fields = @(
            @{ name = "category"; choices = @(@{ label = "Password Reset" }) },
            @{ name = "sub_category"; choices = @{ "password reset" = @(@{ label = "Account Support" }) } },
            @{ name = "item_category"; nested_options = @{ "account support" = @(@{ label = "VPN Access" }) } }
        )
        $taxonomy = Build-Taxonomy -Fields $fields

        $tickets = @(
            @{ id = 1; subject = "Urgent password reset"; description_text = "Need VPN access restored"; category = $null; sub_category = $null; item_category = $null }
        )

        $result = Analyse-Tickets -Tickets $tickets -Taxonomy $taxonomy
        $row = $result.Rows[0]

        $row.suggested_category | Should -Be "Password Reset"
        $row.suggested_sub_category | Should -Be "Account Support"
        $row.suggested_item_category | Should -Be "VPN Access"
    }
}
