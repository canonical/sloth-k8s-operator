Feature: slo-validation

  Scenario: Sloth goes into blocked state when SLO expression is missing a query window
    Given sloth deployed and related together with prometheus and parca
    When parca is configured with an SLO expression missing a query window
    Then sloth is in blocked state with a message indicating that there are invalid SLOs
