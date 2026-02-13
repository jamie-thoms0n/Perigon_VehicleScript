This is the Vehicle script

this code should:
- take a VIN
- find the number plate of that car using an API
- find out a series of information based on another API using the number plate
- return this information


Finding vehicle registration plates from a list of vehicle identification numbers (VINs) then retrieving vehicle data for valid reg plates:
File name (attached) "vin_info_2026" has list of VINs we need data for. 
The challenge here is the multi-step process is all in one python script (attached) "Python Script Vehicles" - and I think is potentially in the wrong order, so a bit harder to untangle. All we're after is: 
VIN numbers + python script and MOT History API (https://documentation.history.mot.api.gov.uk/) to obtain vehicle registration plates from list of VIN numbers.
For list of valid vehicle registration plates just created, use python script and Vehicle Enquiry Service (VES) API (https://developer-portal.driver-vehicle-licensing.api.gov.uk/apis/vehicle-enquiry-service/vehicle-enquiry-service-description.html) to obtain desired additional vehicle information (Fuel type, Engine size, CO2 emissions). 