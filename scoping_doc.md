# Enabling Bidirectional Energy Flow: A Communication Protocol for Vehicle-to-Grid Integration

**Author:** Sebastian Maxwell Weinreich
**Student ID:** 202021483
**Supervisor:** Professor Robert W. Kelsall
**Degree:** MSc in Embedded Systems Engineering
**Institution:** University of Leeds — School of Electronic and Electrical Engineering
**Date:** January 2026

---

## Project Summary

The increasing amount of electric vehicles (EVs) presents both challenges and opportunities for modern power grids. While the growing number of EVs creates substantial additional demand on electrical infrastructure, it simultaneously offers a unique opportunity to leverage distributed energy storage for grid stabilization and renewable energy integration. This project proposes the development of a Vehicle-to-Grid (V2G) communication protocol that enables efficient, reliable, and secure bidirectional energy flow between EVs and the power grid, transforming EVs from passive energy consumers into active distributed energy resources.

The proposed protocol will facilitate real-time data exchange between EVs, charging stations, and grid operators, addressing critical challenges in energy management, grid stability, and system scalability. The architecture will follow OSI model principles while incorporating considerations for battery state-of-charge (SoC), degradation management, user preferences, and grid constraints such as peak demand periods and stability requirements. By enabling seamless communication, the protocol will support dynamic energy management that benefits both individual EV owners and the broader electrical grid infrastructure.

The protocol will be designed for high message delivery success rate, robust operation under adverse conditions, and efficient bandwidth utilization through minimised message sizes. Strong security measures will protect against unauthorised access, ensuring integrity of both vehicle systems and grid infrastructure.

The project scope encompasses the development of a complete communication protocol specification, as an adaption of existing standards such as ISO 15118 (especially ISO 15118-2 and ISO 15118-20) and OCPP (Open Charge Point Protocol). The work will address critical architectural decisions, including whether charging stations should act as intermediaries (communication with vehicles via CAN bus and relaying to the grid via cellular networks or Wi-Fi) or whether direct vehicle-to-grid communication is feasible. A prototype or simulation environment using MATLAB or Python will be developed to validate the protocol's performance under various scenarios.

---

## Project Aim

The aim of this project is to design, develop, and validate a comprehensive V2G communication protocol that enables efficient, reliable, and secure bidirectional energy exchange between EVs and the power grid. This protocol will serve as a practical foundation for large-scale V2G implementation, addressing current technical barriers related to real-time communication, system scalability, security, and interoperability.

The protocol will be designed following OSI architecture principles while incorporating domain-specific requirements including battery management (SoC monitoring and degradation prevention), user preference accommodation, and grid constraint management (peak demand handling and stability maintenance).

By developing and testing the protocol in a simulation environment or a prototype, the project aims to demonstrate feasibility and effectiveness across various operational scenarios. The work will evaluate how to design and implement a communication protocol, to enable efficient, secure, and economically viable bidirectional energy exchange. Ultimately, this project seeks to contribute practical, implementable solutions that can be adopted by EV manufacturers, charging infrastructure providers, and grid operators, accelerating the transition to sustainable energy systems while ensuring grid resilience and reliability.

---

## Project Objectives

- Develop a comprehensive system model identifying key V2G components including EVs, charging stations, power grid infrastructure, and user interactions
- Design a communication protocol architecture based on OSI model principles, defining message formats, data exchange mechanisms, and communication standards for all system interactions
- Establish battery management integration including SoC monitoring, degradation tracking, and usage constraints to protect battery health
- Incorporate user preference (SoC threshold, availability windows) handling into the protocol design
- Optimise bandwidth efficiency through minimised message sizes suitable for high-volume data traffic as EV adoption scales
- Develop a prototype or simulation environment using MATLAB or Python to validate protocol performance under various operational scenarios
- Design for resource efficiency enabling operation on devices with limited computational power and memory
- Ensure user-friendliness and ease of implementation to facilitate adoption by manufacturers and service providers in the V2G ecosystem
- Conduct performance evaluation measuring latency, throughput, reliability, and scalability metrics
