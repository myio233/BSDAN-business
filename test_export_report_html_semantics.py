from exschool_game.export_report_html import _finance_table, render_report_html


def test_finance_table_keeps_loan_and_repayment_semantics() -> None:
    html = _finance_table({"finance_rows": [("银行贷款 / 还款", 1000.0, 2000.0, -500.0, 1500.0)]})

    assert "Bank loan / repayment" in html
    assert ">Bank loan<" not in html


def test_render_report_html_uses_resolved_team_number_for_header_and_highlight() -> None:
    html = render_report_html(
        {
            "team_number": "7",
            "company_name": "Custom Co",
            "key_data": {"markets": {"Shanghai": {"population": 1000.0, "initial_penetration": 0.1}}},
            "report": {
                "round_id": "r1",
                "market_report_subscriptions": ["Shanghai"],
                "market_report_summaries": {
                    "Shanghai": {
                        "population": 1000.0,
                        "penetration": 0.1,
                        "market_size": 100.0,
                        "total_sales_volume": 100.0,
                        "avg_price": 11000.0,
                    }
                },
                "peer_market_tables": {
                    "Shanghai": [
                        {
                            "team": "7",
                            "management_index": 1.1,
                            "agents": 2,
                            "marketing_investment": 1000.0,
                            "quality_index": 1.2,
                            "price": 10000.0,
                            "display_sales_volume": 60.0,
                            "display_marketshare": 0.6,
                        },
                        {
                            "team": "13",
                            "management_index": 1.0,
                            "agents": 1,
                            "marketing_investment": 500.0,
                            "quality_index": 1.0,
                            "price": 12000.0,
                            "display_sales_volume": 40.0,
                            "display_marketshare": 0.4,
                        },
                    ]
                },
            },
        }
    )

    assert 'Team Number:</span> <span class="value">7</span>' in html
    assert "class='highlight-row'><td>7</td>" in html
    assert "class='highlight-row'><td>13</td>" not in html
