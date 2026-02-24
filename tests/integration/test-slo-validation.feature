Feature: slo-validation

  Scenario: Sloth goes into blocked state on SLO/recording rules mismatch
    Given sloth deployed and related together with prometheus and parca
    When we integrate parca with sloth and configure parca to provide invalid SLOs
    Then sloth is in blocked state with a message indicating that there are invalid SLOs

  Scenario: Sloth logs validation warnings on rules that fail to generate
    Given sloth deployed and related together with prometheus and parca
    When we integrate parca with sloth and configure parca to provide invalid SLOs
    Then sloth logs validation warnings for the SLOs that are failing
